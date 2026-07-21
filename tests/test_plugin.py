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
            "payload/package.json": '{"engines":{"node":">=24"}}\n',
            "payload/pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            "payload/agent/agent.ts": "const url = process.env.AGENTOUR_URL;\n",
            "payload/agent/instructions.md": "# Demo\n缺少信息时调用 ask_question。\n",
            "payload/agent/sandbox/sandbox.ts": "export default {};\n",
        }
        manifest = {
            "id": "demo", "name": "Demo", "version": "0.1.0", "runtime": "eve",
            "capabilities": ["review"], "description": "Demo", "pricing": {"model": "per_run", "amount_credits": 5},
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

    def test_fixed_platform_urls(self):
        api = load_api()
        self.assertEqual(api.base_url("local"), "http://127.0.0.1:8600")
        self.assertEqual(api.base_url("competition"), "https://agentour.ai")
        self.assertIn("remote-build", (PLUGIN / "scripts/agentour_api.py").read_text())
        self.assertIn("compiler-tasks", (PLUGIN / "scripts/agentour_api.py").read_text())

    def test_compiler_skill_supports_update_and_adaptive_discovery(self):
        skill = (PLUGIN / "skills/agentour-compiler/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("更新已发布的 Agent", skill)
        self.assertIn("请尽可能完整地讲讲你想做的 Agent", skill)
        self.assertIn("/v1/dev/compiler-tasks", skill)
        self.assertIn("checkpoint-package", skill)

    def test_compiler_task_commands_send_expected_contract(self):
        api = load_api()
        args = SimpleNamespace(platform="competition", operation="update",
                               agent_id="demo", workspace_id="ws-hash",
                               state='{"stage":"discovery"}')
        with mock.patch.object(api, "authenticated", return_value={"id": "cmp_1"}) as call:
            api.cmd_create_compiler_task(args)
        self.assertEqual(call.call_args.args[1], "/v1/dev/compiler-tasks")
        self.assertEqual(call.call_args.kwargs["body"]["operation"], "update")

    def test_template_requires_session_scoped_runtime_token(self):
        template = (PLUGIN / "templates/agent.ts").read_text(encoding="utf-8")
        self.assertIn("process.env.AGENTOUR_RUNTIME_TOKEN", template)
        self.assertNotIn("process.env.AGENTOUR_RUNTIME_KEY", template)
        self.assertNotIn("build-only-placeholder", template)

    def test_token_requires_at_prefix(self):
        api = load_api()
        old = os.environ.get("AGENTOUR_TOKEN")
        os.environ["AGENTOUR_TOKEN"] = "wrong"
        try:
            with self.assertRaises(SystemExit):
                api.request("competition", "/v1/dev/me", auth=True)
        finally:
            if old is None:
                os.environ.pop("AGENTOUR_TOKEN", None)
            else:
                os.environ["AGENTOUR_TOKEN"] = old

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
                module.set_token("local", "at_local_token_value")
                module.set_token("competition", "at_competition_token_value")
                self.assertEqual(module.get_token("local"), "at_local_token_value")
                self.assertEqual(module.get_token("competition"), "at_competition_token_value")
                module.delete_token("local")
                self.assertEqual(module.get_token("local"), "")
                self.assertEqual(module.get_token("competition"), "at_competition_token_value")
            finally:
                for key, value in old.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value

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
            args = SimpleNamespace(package=str(package), platform="competition",
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
