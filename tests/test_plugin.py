from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "agentour-compiler"


def load_api():
    path = PLUGIN / "scripts" / "agentour_api.py"
    spec = importlib.util.spec_from_file_location("agentour_api", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class PluginTests(unittest.TestCase):
    def make_package(self, root: pathlib.Path):
        files = {
            "README.md": "# Demo\n",
            "RELEASE.md": "# 0.1.0\n",
            "tests/smoke.yaml": 'schema_version: 1\ncases:\n  - send: "x"\n    expect_contains: "ok"\n',
            "payload/package.json": '{"engines":{"node":">=24"},"packageManager":"pnpm@10.23.0"}\n',
            "payload/pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            "payload/pnpm-workspace.yaml": "packages:\n  - '.'\nminimumReleaseAge: 1440\nallowBuilds: {}\n",
            "payload/agent/agent.ts": "const url = process.env.AGENTOUR_URL;\n",
            "payload/agent/instructions.md": "# Demo\n缺少信息时调用 ask_question。工具失败时不得声称成功，并说明下一步。\n",
            "payload/agent/sandbox/sandbox.ts": "export default {};\n",
        }
        manifest = {
            "compiler_contract_version": 4,
            "id": "demo", "name": "Demo", "version": "0.1.0", "runtime": "eve",
            "capabilities": ["review"], "description": "Demo", "pricing": {"model": "per_run", "amount_credits": 5},
            "deliverable": {"required": True, "formats": ["markdown"]},
            "examples": ["完整输入一", "完整输入二"],
            "runtime_ui": {
                "startup_message": "正在启动审查助手…",
                "default_working_message": "正在分析内容…",
                "capabilities": {"review": {"display_name": "内容审查", "loading_message": "正在加载内容审查能力…"}},
            },
        }
        files["agentour.json"] = json.dumps(manifest, ensure_ascii=False)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_manifest_and_marketplace_names_match(self):
        manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
        market = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
        self.assertEqual(manifest["name"], "agentour-compiler")
        self.assertEqual(market["plugins"][0]["name"], manifest["name"])

    def test_api_client_version_matches_installed_manifest(self):
        api = load_api()
        manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(api.PLUGIN_VERSION, manifest["version"])

    def test_same_marketplace_version_never_requires_restart(self):
        api = load_api()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "version": api.PLUGIN_VERSION
        }).encode()
        with mock.patch.object(api.urllib.request, "urlopen", return_value=response), \
             mock.patch.object(api.subprocess, "run") as installer:
            result = api.check_update(auto=True)
        self.assertFalse(result["outdated"])
        self.assertFalse(result["updated"])
        self.assertNotIn("restart_required", result)
        installer.assert_not_called()

    def test_fixed_platform_urls(self):
        api = load_api()
        self.assertEqual(api.base_url("test"), "https://test.agentour.ai")
        self.assertEqual(api.base_url("production"), "https://agentour.ai")
        self.assertIn("remote-build", (PLUGIN / "scripts/agentour_api.py").read_text())
        self.assertIn("compiler-tasks", (PLUGIN / "scripts/agentour_api.py").read_text())
        self.assertIn("build-preflight", (PLUGIN / "scripts/agentour_api.py").read_text())
        self.assertIn("bootstrap", (PLUGIN / "scripts/agentour_api.py").read_text())

    def test_bootstrap_requires_platform_before_interview(self):
        api = load_api()
        args = SimpleNamespace(target_platform=None, platform="production")
        with mock.patch.object(api, "check_update", return_value={
                "checked": True, "outdated": False, "updated": False}), \
             mock.patch.object(api.pathlib.Path, "is_file", return_value=False):
            with mock.patch("builtins.print") as output:
                api.cmd_bootstrap(args)
        payload = json.loads(output.call_args.args[0])
        self.assertTrue(payload["platform_choice_required"])
        self.assertFalse(payload["ready_for_interview"])

    def test_static_validator_generates_platform_package_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            result = subprocess.run([
                sys.executable, str(PLUGIN / "scripts/validate_package.py"), str(package)
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lock = json.loads((package / "package.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["generated_by"], "agentourcore.lockfile/1")
            self.assertNotIn("package.lock", lock["files"])

    def test_compiler_skill_supports_update_and_adaptive_discovery(self):
        skill = (PLUGIN / "skills/agentour-compiler/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("更新已发布的 Agent", skill)
        self.assertIn("请尽可能完整地讲讲你想做的 Agent", skill)
        self.assertIn("/v1/dev/compiler-tasks", skill)
        self.assertIn("checkpoint-package", skill)

    def test_compiler_task_commands_send_expected_contract(self):
        api = load_api()
        args = SimpleNamespace(platform="production", operation="update",
                               agent_id="demo", workspace_id="ws-hash",
                               state='{"stage":"discovery"}')
        with mock.patch.object(api, "authenticated", return_value={"id": "cmp_1"}) as call:
            api.cmd_create_compiler_task(args)
        create_call = call.call_args_list[0]
        self.assertEqual(create_call.args[1], "/v1/dev/compiler-tasks")
        self.assertEqual(create_call.kwargs["body"]["operation"], "update")

    def test_template_requires_session_scoped_runtime_token(self):
        template = (PLUGIN / "templates/agent.ts").read_text(encoding="utf-8")
        self.assertIn("process.env.AGENTOUR_RUNTIME_TOKEN", template)
        self.assertNotIn("process.env.AGENTOUR_RUNTIME_KEY", template)
        self.assertNotIn("build-only-placeholder", template)
        self.assertNotIn("system:", template)
        self.assertNotIn("throw new Error", template)
        package = json.loads((PLUGIN / "templates/package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["packageManager"], "pnpm@10.23.0")
        self.assertTrue(all(not version.startswith(("^", "~"))
                            for version in package["dependencies"].values()))
        workspace = (PLUGIN / "templates/pnpm-workspace.yaml").read_text(encoding="utf-8")
        self.assertIn("allowBuilds:", workspace)
        self.assertIn("minimumReleaseAge: 1440", workspace)

    def test_token_requires_at_prefix(self):
        api = load_api()
        old = os.environ.get("AGENTOUR_TOKEN")
        os.environ["AGENTOUR_TOKEN"] = "wrong"
        try:
            with self.assertRaises(SystemExit):
                api.request("production", "/v1/dev/me", auth=True)
        finally:
            if old is None:
                os.environ.pop("AGENTOUR_TOKEN", None)
            else:
                os.environ["AGENTOUR_TOKEN"] = old

    def test_flight_recorder_persists_redacted_job_evidence(self):
        script = PLUGIN / "scripts/flight_recorder.py"
        spec = importlib.util.spec_from_file_location("agentour_flight_test", script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("AGENTOUR_COMPILER_FLIGHT_LOG")
            os.environ["AGENTOUR_COMPILER_FLIGHT_LOG"] = str(pathlib.Path(td) / "flight.json")
            try:
                module.record("failure", error="Bearer secret-value", api_key="sk-secret-value")
                module.record_job_sample("validation", {
                    "id": "val_1", "status": "running",
                    "report": {"heartbeat_at": 12, "stage": "smoke"}},
                    poll_count=3, unchanged_seconds=20, poll_interval_seconds=2)
                data = module.read()
            finally:
                if old is None: os.environ.pop("AGENTOUR_COMPILER_FLIGHT_LOG", None)
                else: os.environ["AGENTOUR_COMPILER_FLIGHT_LOG"] = old
        self.assertEqual(data["events"][0]["api_key"], "[REDACTED]")
        self.assertNotIn("secret-value", json.dumps(data))
        self.assertEqual(data["events"][1]["poll_count"], 3)

    def test_default_flight_log_is_outside_package(self):
        script = PLUGIN / "scripts/flight_recorder.py"
        spec = importlib.util.spec_from_file_location("agentour_flight_path_test", script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td, mock.patch.object(module.pathlib.Path, "cwd", return_value=pathlib.Path(td)):
            old = os.environ.pop("AGENTOUR_COMPILER_FLIGHT_LOG", None)
            try:
                self.assertFalse(module._path().is_relative_to(pathlib.Path(td)))
            finally:
                if old is not None: os.environ["AGENTOUR_COMPILER_FLIGHT_LOG"] = old

    def test_job_poll_transport_failure_resumes_same_job(self):
        api = load_api()
        args = SimpleNamespace(platform="production")
        with mock.patch.object(api, "authenticated", side_effect=api.APITransportError("timeout")), \
             mock.patch.object(api, "record_flight") as record:
            self.assertIsNone(api.poll_job(args, "/v1/dev/builds/bld_1", "remote_build", "bld_1"))
        self.assertEqual(record.call_args.kwargs["job_id"], "bld_1")
        self.assertTrue(record.call_args.kwargs["retrying_same_job"])

    def test_credentials_are_separated_by_platform(self):
        path = PLUGIN / "scripts/credential_store.py"
        spec = importlib.util.spec_from_file_location("credential_store_test", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            old = {key: os.environ.get(key) for key in ("XDG_CONFIG_HOME", "AGENTOUR_CREDENTIAL_BACKEND")}
            os.environ["XDG_CONFIG_HOME"] = temp
            os.environ["AGENTOUR_CREDENTIAL_BACKEND"] = "restricted-file"
            try:
                module.set_token("test", "at_test_token_value")
                module.set_token("production", "at_production_token_value")
                self.assertEqual(module.get_token("test"), "at_test_token_value")
                self.assertEqual(module.get_token("production"), "at_production_token_value")
                module.delete_token("test")
                self.assertEqual(module.get_token("test"), "")
                self.assertEqual(module.get_token("production"), "at_production_token_value")
            finally:
                for key, value in old.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value

    def test_failed_wsl_keychain_falls_back_to_stable_restricted_file(self):
        path = PLUGIN / "scripts/credential_store.py"
        spec = importlib.util.spec_from_file_location("credential_store_fallback", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": temp,
                                          "AGENTOUR_CREDENTIAL_BACKEND": "windows-credential-manager"}), \
             mock.patch.object(module, "_ps", return_value=SimpleNamespace(
                 returncode=1, stdout="", stderr="unavailable")):
            self.assertEqual(module.set_token("production", "at_persistent_value"), "restricted-file")
            self.assertEqual(module.get_token("production"), "at_persistent_value")
            credential = pathlib.Path(temp) / "agentour/credentials.json"
            self.assertEqual(credential.stat().st_mode & 0o777, 0o600)

    def test_package_tarball(self):
        api = load_api()
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            payload, stats = api.package_payload(package)
            self.assertEqual(payload[:2], b"\x1f\x8b")
            self.assertGreater(stats["files"], 0)
            with tarfile.open(fileobj=__import__("io").BytesIO(payload), mode="r:gz") as archive:
                self.assertIn("demo/agentour.json", archive.getnames())

    def test_package_tarball_excludes_generated_dependencies(self):
        api = load_api()
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            generated = package / "payload/node_modules/pkg/index.js"
            generated.parent.mkdir(parents=True)
            generated.write_text("generated")
            payload, _ = api.package_payload(package)
            with tarfile.open(fileobj=__import__("io").BytesIO(payload), mode="r:gz") as archive:
                self.assertFalse(any("node_modules" in name for name in archive.getnames()))

    def test_remote_build_waits_for_structured_success(self):
        api = load_api()
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            args = SimpleNamespace(package=str(package), platform="production",
                                   no_wait=False, timeout=10, poll_interval=0)
            with mock.patch.object(api, "request", return_value={"job_id": "bld_1", "status": "queued"}), \
                 mock.patch.object(api, "authenticated", return_value={
                     "job_id": "bld_1", "status": "succeeded",
                     "data": {"gates": [{"gate": "remote_build", "status": "pass"}]}}):
                api.cmd_remote_build(args)

    def test_valid_package(self):
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            result = subprocess.run([
                sys.executable, str(PLUGIN / "scripts/validate_package.py"), str(package)
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_internal_status_term_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            manifest_path = package / "agentour.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["runtime_ui"]["capabilities"]["review"]["loading_message"] = "load skill review"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(PLUGIN / "scripts/validate_package.py"), str(package)
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Internal terminology", result.stdout)

    def test_fidelity_critical_failure_is_grade_d(self):
        path = PLUGIN / "scripts" / "fidelity_report.py"
        spec = importlib.util.spec_from_file_location("fidelity_report", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        score, grade = module.calculate({
            "critical_assertions": {"failed": 1},
            "dimensions": {key: 100 for key in module.WEIGHTS},
        })
        self.assertIsNone(score)
        self.assertEqual(grade, "D")

    def test_fidelity_weighted_grade(self):
        path = PLUGIN / "scripts" / "fidelity_report.py"
        spec = importlib.util.spec_from_file_location("fidelity_report_score", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        score, grade = module.calculate({
            "critical_assertions": {"failed": 0},
            "dimensions": {key: 92 for key in module.WEIGHTS},
        })
        self.assertEqual(score, 92)
        self.assertEqual(grade, "A")


if __name__ == "__main__":
    unittest.main()
