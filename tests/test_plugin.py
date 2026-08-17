from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
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


def load_lark_preflight():
    path = PLUGIN / "scripts" / "lark_cli_preflight.py"
    spec = importlib.util.spec_from_file_location("lark_cli_preflight", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def load_release_verifier():
    path = ROOT / "scripts" / "verify_plugin_release.py"
    spec = importlib.util.spec_from_file_location("verify_plugin_release", path)
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
            "payload/agent/instructions.md": (
                "# Demo\n缺少信息时调用 ask_question，使会话进入 input_requested。"
                "用户补充后重新检查剩余缺口，继续询问下一项；缺信息时不得标记完成或输出最终交付物。"
                "只有任务成功、无法继续的明确失败或用户明确取消才能结束。"
                "一次形成完整执行计划，每个 Skill 和 Schema 只加载一次并复用。"
                "同类操作批量执行，禁止每条记录单独触发模型规划。"
                "使用幂等键支持中断恢复，避免重试时重复创建。"
                "工具失败时不得声称成功，并说明下一步。\n"
            ),
        }
        manifest = {
            "compiler_contract_version": 4,
            "id": "demo", "name": "Demo", "version": "0.1.0", "runtime": "eve",
            "capabilities": ["review"], "description": "Demo", "pricing": {"model": "per_run", "amount_credits": 5},
            "deliverable": {"required": True, "formats": ["markdown"]},
            "interaction_policy": {"execution_mode":"clarify_until_ready",
                                   "dangerous_action_approval":"always"},
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
        manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "agentour-compiler")
        self.assertEqual(market["plugins"][0]["name"], manifest["name"])
        self.assertTrue(manifest["version"].startswith("0.9.1+codex."))

    def test_release_integrity_snapshot_matches_candidate(self):
        verifier = load_release_verifier()
        result = verifier.verify_source(PLUGIN, ROOT / ".agents/plugins/marketplace.json")
        self.assertEqual(result["plugin_name"], "agentour-compiler")
        self.assertEqual(result["plugin_version"], json.loads(
            (PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"])

    def test_release_integrity_normalizes_text_line_endings_only(self):
        verifier = load_release_verifier()
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            lf = root / "lf.txt"
            crlf = root / "crlf.txt"
            changed = root / "changed.txt"
            lf.write_bytes(b"first\nsecond\n")
            crlf.write_bytes(b"first\r\nsecond\r\n")
            changed.write_bytes(b"first\nchanged\n")
            self.assertEqual(verifier._sha256(lf), verifier._sha256(crlf))
            self.assertNotEqual(verifier._sha256(lf), verifier._sha256(changed))

    def test_api_client_version_matches_installed_manifest(self):
        api = load_api()
        manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
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

    def test_new_patch_and_changed_cache_identity_trigger_update(self):
        api = load_api()
        cases = (
            ("0.9.0+codex.old", "0.9.1+codex.new", "newer_semver"),
            ("0.9.1+codex.old", "0.9.1+codex.new", "cache_identity_changed"),
        )
        for current, latest, reason in cases:
            with self.subTest(current=current, latest=latest), \
                 mock.patch.object(api, "PLUGIN_VERSION", current), \
                 mock.patch.object(api.urllib.request, "urlopen") as urlopen, \
                 mock.patch.object(api.subprocess, "run", return_value=SimpleNamespace(
                     returncode=0, stdout="", stderr="")) as installer:
                response = mock.MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps({
                    "version": latest}).encode()
                urlopen.return_value = response
                result = api.check_update(auto=True)
            self.assertTrue(result["outdated"])
            self.assertTrue(result["updated"])
            self.assertTrue(result["restart_required"])
            self.assertEqual(result["comparison_reason"], reason)
            self.assertEqual(installer.call_count, 2)

    def test_current_newer_version_does_not_downgrade(self):
        api = load_api()
        outdated, reason = api.plugin_update_decision(
            "0.9.2+codex.current", "0.9.1+codex.remote")
        self.assertFalse(outdated)
        self.assertEqual(reason, "current_semver_newer")
        outdated, reason = api.plugin_update_decision(
            "0.9.1+codex.20260817062629", "0.9.1+codex.20260816000000")
        self.assertFalse(outdated)
        self.assertEqual(reason, "current_cache_identity_newer")

    def test_invalid_remote_plugin_version_blocks_bootstrap(self):
        api = load_api()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"version":"not-semver"}'
        with mock.patch.object(api.urllib.request, "urlopen", return_value=response):
            result = api.check_update(auto=False)
        self.assertTrue(result["blocked"])
        self.assertFalse(result["outdated"])

    def test_cache_verifier_fails_closed_on_installed_content_drift(self):
        verifier = load_release_verifier()
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            plugin = root / "plugins/agentour-compiler"
            marketplace = root / ".agents/plugins/marketplace.json"
            marketplace.parent.mkdir(parents=True)
            shutil.copytree(PLUGIN, plugin)
            marketplace.write_text((ROOT / ".agents/plugins/marketplace.json").read_text(
                encoding="utf-8"), encoding="utf-8")
            snapshot = verifier.write_snapshot(plugin, marketplace)
            cache_root = root / "cache"
            installed = (cache_root / snapshot["marketplace_name"] /
                         snapshot["plugin_name"] / snapshot["plugin_version"])
            shutil.copytree(plugin, installed)
            verified = verifier.verify_cache(plugin, marketplace, cache_root)
            self.assertEqual(pathlib.Path(verified["installed_path"]), installed)
            with (installed / "scripts/agentour_api.py").open("a", encoding="utf-8") as file:
                file.write("\n# drift\n")
            with self.assertRaises(verifier.ReleaseIntegrityError):
                verifier.verify_cache(plugin, marketplace, cache_root)
            shutil.copy2(plugin / "scripts/agentour_api.py",
                         installed / "scripts/agentour_api.py")
            (installed / "skills/agentour-compiler/STALE.md").write_text(
                "stale", encoding="utf-8")
            with self.assertRaises(verifier.ReleaseIntegrityError):
                verifier.verify_cache(plugin, marketplace, cache_root)

    def test_release_verifier_rejects_manifest_and_marketplace_drift(self):
        verifier = load_release_verifier()
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            plugin = root / "plugins/agentour-compiler"
            marketplace = root / ".agents/plugins/marketplace.json"
            marketplace.parent.mkdir(parents=True)
            shutil.copytree(PLUGIN, plugin)
            marketplace.write_text((ROOT / ".agents/plugins/marketplace.json").read_text(
                encoding="utf-8"), encoding="utf-8")
            verifier.write_snapshot(plugin, marketplace)
            manifest_path = plugin / ".codex-plugin/plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "0.9.2+codex.drift"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(verifier.ReleaseIntegrityError):
                verifier.verify_source(plugin, marketplace)
            manifest["version"] = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(
                encoding="utf-8"))["version"]
            shutil.copy2(PLUGIN / ".codex-plugin/plugin.json", manifest_path)
            verifier.write_snapshot(plugin, marketplace)
            market = json.loads(marketplace.read_text(encoding="utf-8"))
            market["plugins"][0]["source"]["path"] = "./plugins/wrong"
            marketplace.write_text(json.dumps(market), encoding="utf-8")
            with self.assertRaises(verifier.ReleaseIntegrityError):
                verifier.verify_source(plugin, marketplace)

    def test_fixed_platform_urls(self):
        api = load_api()
        self.assertEqual(api.base_url("test"), "https://test.agentour.ai")
        self.assertEqual(api.base_url("production"), "https://agentour.ai")
        self.assertIn("remote-build", (PLUGIN / "scripts/agentour_api.py").read_text(encoding="utf-8"))
        self.assertIn("compiler-tasks", (PLUGIN / "scripts/agentour_api.py").read_text(encoding="utf-8"))
        self.assertIn("build-preflight", (PLUGIN / "scripts/agentour_api.py").read_text(encoding="utf-8"))
        self.assertIn("bootstrap", (PLUGIN / "scripts/agentour_api.py").read_text(encoding="utf-8"))

    def test_forge_repository_uses_frozen_developer_contract(self):
        api = load_api()
        args = SimpleNamespace(platform="test", repository_id="repo_1")
        with mock.patch.object(api, "authenticated", return_value={"repository_id": "repo_1"}) as request, \
             mock.patch.object(api, "record_flight"), mock.patch("builtins.print"):
            api.cmd_repository(args)
        request.assert_called_once_with(args, "/v1/dev/repositories/repo_1")

    def test_repository_control_commands_use_contract_1_routes(self):
        api = load_api()
        list_args = SimpleNamespace(platform="test", cursor="cursor_1", limit=25)
        create_args = SimpleNamespace(platform="test", kind="agent", name="agent-demo",
                                      default_branch="main", visibility="private")
        status_args = SimpleNamespace(platform="test", repository_id="repo/1")
        with mock.patch.object(api, "authenticated", side_effect=[
                {"items": [], "forge_contract_version": "1.0"},
                {"repository_id": "repo_1", "state": "provisioning",
                 "forge_contract_version": "1.0"},
                {"repository_id": "repo/1", "state": "active",
                 "forge_contract_version": "1.0"},
            ]) as request, mock.patch.object(api, "record_flight"), \
             mock.patch("builtins.print"):
            api.cmd_repositories(list_args)
            api.cmd_repository_create(create_args)
            api.cmd_repository_status(status_args)
        listing, creation, status = request.call_args_list
        self.assertEqual(listing.args[1], "/v1/forge/repositories?limit=25&cursor=cursor_1")
        self.assertEqual(creation.args[1], "/v1/forge/repositories")
        self.assertEqual(creation.kwargs["method"], "POST")
        self.assertEqual(creation.kwargs["body"], {
            "kind": "agent", "canonical_name": "agent-demo",
            "default_branch": "main", "visibility": "private",
        })
        self.assertTrue(creation.kwargs["idempotency_key"].startswith(
            "agentour-repository-create-"))
        self.assertEqual(status.args[1], "/v1/forge/repositories/repo%2F1")

    def test_git_credential_exchange_is_one_time_and_never_recorded(self):
        api = load_api()
        args = SimpleNamespace(platform="test", credential_ttl=900,
                               credential_max_uses=20)
        response = {"credential_id": "gcr_1", "username": "git-user",
                    "credential": "gcr_1.secret-value",
                    "clone_url": "https://forge.example.test/git/repo_1",
                    "expires_at": "2026-08-17T00:00:00Z"}
        with mock.patch.object(api, "authenticated", return_value=response) as request:
            first = api.issue_git_credential(args, "repo_1", "read")
            first_key = request.call_args.kwargs["idempotency_key"]
            second = api.issue_git_credential(args, "repo_1", "read")
            second_key = request.call_args.kwargs["idempotency_key"]
        self.assertEqual(first, response)
        self.assertEqual(second, response)
        self.assertNotEqual(first_key, second_key)
        self.assertTrue(first_key.startswith("agentour-git-credential-"))
        self.assertEqual(request.call_args.args[1], "/v1/forge/git-credentials")
        self.assertEqual(request.call_args.kwargs["body"], {
            "repository_id": "repo_1", "action": "read",
            "ttl_seconds": 900, "max_uses": 20,
        })

    def test_git_credential_limits_fail_before_network_exchange(self):
        api = load_api()
        with mock.patch.object(api, "authenticated") as request:
            for ttl, max_uses in ((59, 20), (901, 20), (900, 0), (900, 101)):
                with self.subTest(ttl=ttl, max_uses=max_uses), \
                     self.assertRaises(SystemExit):
                    api.issue_git_credential(SimpleNamespace(
                        credential_ttl=ttl, credential_max_uses=max_uses),
                        "repo_1", "read")
        request.assert_not_called()

    def test_git_runner_keeps_plaintext_out_of_command_and_helper_file(self):
        api = load_api()
        credential = {"username": "git-user", "credential": "gcr_1.secret-value"}
        completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        observed = {}
        def fake_run(command, **kwargs):
            observed["command"] = command
            observed["environment"] = kwargs["env"]
            observed["helper_text"] = pathlib.Path(kwargs["env"]["GIT_ASKPASS"]).read_text(
                encoding="utf-8")
            return completed
        with mock.patch.object(api.subprocess, "run", side_effect=fake_run):
            result = api.run_git_with_credential(
                ["git", "clone", "https://forge.example.test/git/repo_1", "repo"],
                credential, cwd=None, timeout=30)
        self.assertIs(result, completed)
        command = observed["command"]
        environment = observed["environment"]
        self.assertNotIn(credential["credential"], " ".join(command))
        self.assertIn("credential.helper=", command)
        self.assertTrue(any(item.startswith("core.hooksPath=") for item in command))
        self.assertEqual(environment["AGENTOUR_GIT_CREDENTIAL"], credential["credential"])
        self.assertNotIn(credential["credential"], observed["helper_text"])

    def test_git_runner_redacts_credentials_from_failure_output(self):
        api = load_api()
        secret = "opaque-credential-value"
        completed = SimpleNamespace(
            returncode=1, stdout=f"failed {secret}",
            stderr=" Bearer ak_example123456789")
        with mock.patch.object(api.subprocess, "run", return_value=completed), \
             self.assertRaises(SystemExit) as raised:
            api.run_git_with_credential(
                ["git", "push", "https://forge.example.test/git/repo_1", "HEAD"],
                {"username": "git-user", "credential": secret}, cwd=None, timeout=30)
        message = str(raised.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn("ak_example123456789", message)
        self.assertIn("[REDACTED]", message)

    def test_git_clone_rejects_a_file_destination_before_credential_exchange(self):
        api = load_api()
        with tempfile.TemporaryDirectory() as td:
            destination = pathlib.Path(td) / "existing-file"
            destination.write_text("not a directory", encoding="utf-8")
            args = SimpleNamespace(repository_id="repo_1", destination=str(destination),
                                   commit_sha="a" * 40)
            with mock.patch.object(api, "issue_git_credential") as issue, \
                 self.assertRaises(SystemExit) as raised:
                api.cmd_git_clone(args)
        self.assertIn("not a directory", str(raised.exception))
        issue.assert_not_called()

    def test_git_clone_redacts_checkout_failure_output(self):
        api = load_api()
        secret = "opaque-checkout-credential"
        credential = {"credential_id": "gcr_1", "username": "git-user",
                      "credential": secret,
                      "clone_url": "https://forge.example.test/git/repo_1",
                      "expires_at": "2026-08-17T00:00:00Z"}
        with tempfile.TemporaryDirectory() as td:
            destination = pathlib.Path(td) / "clone"
            args = SimpleNamespace(repository_id="repo_1", destination=str(destination),
                                   commit_sha="a" * 40, timeout=30)
            with mock.patch.object(api, "issue_git_credential", return_value=credential), \
                 mock.patch.object(api, "run_git_with_credential"), \
                 mock.patch.object(api.subprocess, "run", return_value=SimpleNamespace(
                     returncode=1, stdout=f"failed {secret}", stderr="")), \
                 self.assertRaises(SystemExit) as raised:
                api.cmd_git_clone(args)
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))

    def test_full_commit_sha_accepts_only_sha1_or_sha256_lengths(self):
        api = load_api()
        self.assertTrue(api.is_full_commit_sha("a" * 40))
        self.assertTrue(api.is_full_commit_sha("B" * 64))
        for length in (39, 41, 63, 65):
            with self.subTest(length=length):
                self.assertFalse(api.is_full_commit_sha("a" * length))

    def test_structured_api_error_preserves_codes_and_redacts_tokens(self):
        api = load_api()
        detail = json.dumps({
            "error": "failed for Bearer ak_example123456789",
            "error_code": "REPOSITORY_NOT_FOUND", "request_id": "req_1",
            "correlation_id": "corr_1", "stage": "authorization",
            "target_service": "core", "retryable": False,
            "details": {"password": "opaque-password",
                        "context": "github_pat_example123456789"},
            "internal_payload": {"credential": "gcr_secret.value"},
        })
        message = api.format_api_error(404, detail)
        self.assertIn("Agentour API 404", message)
        self.assertIn("REPOSITORY_NOT_FOUND", message)
        self.assertIn("req_1", message)
        self.assertNotIn("ak_example123456789", message)
        self.assertNotIn("opaque-password", message)
        self.assertNotIn("github_pat_example123456789", message)
        self.assertNotIn("internal_payload", message)

    def test_forge_creation_commands_send_stable_idempotency_keys(self):
        api = load_api()
        commit = "a" * 40
        source_args = SimpleNamespace(platform="test", repository_id="repo_1", commit_sha=commit,
                                      pull_request_number=42)
        build_args = SimpleNamespace(platform="test", source_revision_id="sr_1")
        release_args = SimpleNamespace(platform="test", package_id="pkg_1", version="1.2.3",
                                       source_revision_id="sr_1", visibility="private", tag="")
        with mock.patch.object(api, "authenticated", return_value={"source_revision_id": "sr_1"}) as request, \
             mock.patch.object(api, "record_flight"), mock.patch("builtins.print"):
            api.cmd_source_revision(source_args)
            first = request.call_args
            api.cmd_source_revision(source_args)
            second = request.call_args
        self.assertEqual(first.kwargs["body"], {"commit_sha": commit, "pull_request_number": 42})
        self.assertEqual(first.kwargs["idempotency_key"], second.kwargs["idempotency_key"])
        self.assertTrue(first.kwargs["idempotency_key"].startswith("agentour-source-revision-"))

        changed_pr = SimpleNamespace(platform="test", repository_id="repo_1", commit_sha=commit,
                                     pull_request_number=43)
        with mock.patch.object(api, "authenticated", return_value={"source_revision_id": "sr_2"}) as changed_request, \
             mock.patch.object(api, "record_flight"), mock.patch("builtins.print"):
            api.cmd_source_revision(changed_pr)
        self.assertNotEqual(first.kwargs["idempotency_key"], changed_request.call_args.kwargs["idempotency_key"])

        with mock.patch.object(api, "authenticated", side_effect=[
                {"build_id": "bld_1"}, {"eval_run_id": "evr_1"}, {"release_id": "rel_1"}]) as request, \
             mock.patch.object(api, "record_flight"), mock.patch("builtins.print"):
            api.cmd_source_build(build_args)
            api.cmd_source_eval(build_args)
            api.cmd_release(release_args)
        build_call, eval_call, release_call = request.call_args_list
        self.assertEqual(build_call.args[1], "/v1/dev/source-revisions/sr_1/builds")
        self.assertEqual(eval_call.args[1], "/v1/dev/source-revisions/sr_1/eval-runs")
        self.assertTrue(build_call.kwargs["idempotency_key"].startswith("agentour-source-build-"))
        self.assertTrue(eval_call.kwargs["idempotency_key"].startswith("agentour-source-eval-"))
        self.assertEqual(release_call.kwargs["body"], {
            "package_id": "pkg_1", "version": "1.2.3", "source_revision_id": "sr_1",
            "visibility": "private", "tag": None,
        })
        self.assertTrue(release_call.kwargs["idempotency_key"].startswith("agentour-release-"))

    def test_authenticated_places_idempotency_key_in_http_header(self):
        api = load_api()
        args = SimpleNamespace(platform="test")
        with mock.patch.object(api, "request", return_value={}) as request:
            api.authenticated(args, "/v1/dev/releases", method="POST", body={"x": 1},
                              idempotency_key="agentour-release-example")
        self.assertEqual(request.call_args.kwargs["extra_headers"], {
            "Idempotency-Key": "agentour-release-example"
        })

    def test_forge_status_commands_resume_existing_remote_records(self):
        api = load_api()
        args = SimpleNamespace(platform="production", source_revision_id="sr_1")
        build_args = SimpleNamespace(platform="production", build_id="bld/1")
        eval_args = SimpleNamespace(platform="production", eval_run_id="evr/1")
        release_args = SimpleNamespace(platform="production", release_id="rel_1")
        with mock.patch.object(api, "authenticated", side_effect=[{}, {}, {}, {}]) as request, \
             mock.patch.object(api, "record_flight"), mock.patch("builtins.print"):
            api.cmd_source_revision_status(args)
            api.cmd_source_build_status(build_args)
            api.cmd_source_eval_status(eval_args)
            api.cmd_release_status(release_args)
        self.assertEqual(request.call_args_list[0].args[1], "/v1/dev/source-revisions/sr_1")
        self.assertEqual(request.call_args_list[1].args[1], "/v1/dev/builds/bld%2F1")
        self.assertEqual(request.call_args_list[2].args[1], "/v1/dev/eval-runs/evr%2F1")
        self.assertEqual(request.call_args_list[3].args[1], "/v1/dev/releases/rel_1")

    def test_forge_status_commands_record_redacted_lineage_only(self):
        api = load_api()
        build_args = SimpleNamespace(platform="test", build_id="bld_1")
        eval_args = SimpleNamespace(platform="test", eval_run_id="evr_1")
        with mock.patch.object(api, "authenticated", side_effect=[
                {"build_id": "bld_1", "source_revision_id": "sr_1",
                 "status": "blocked", "error_code": "SOURCE_BUNDLE_UNAVAILABLE",
                 "contract_version": "1.0"},
                {"eval_run_id": "evr_1", "source_revision_id": "sr_1",
                 "build_id": "bld_1", "status": "queued", "error_code": None,
                 "contract_version": "1.0"},
            ]), mock.patch.object(api, "record_flight") as record, \
             mock.patch("builtins.print"):
            api.cmd_source_build_status(build_args)
            api.cmd_source_eval_status(eval_args)
        build_event, eval_event = record.call_args_list
        self.assertEqual(build_event.args[0], "source_build_read")
        self.assertEqual(build_event.kwargs["remote_job_id"], "bld_1")
        self.assertEqual(eval_event.args[0], "source_eval_read")
        self.assertEqual(eval_event.kwargs["build_id"], "bld_1")
        self.assertNotIn("token", build_event.kwargs)
        self.assertNotIn("token", eval_event.kwargs)

    def test_release_transitions_use_frozen_routes_and_stable_idempotency(self):
        api = load_api()
        args = SimpleNamespace(platform="test", release_id="rel/1")
        actions = ("submit-review", "approve", "activate", "withdraw", "rollback")
        with mock.patch.object(api, "authenticated", return_value={
                "release_id": "rel/1", "status": "pending_review",
                "contract_version": "1.0"}) as request, \
             mock.patch.object(api, "record_flight") as record, \
             mock.patch("builtins.print"):
            for action in actions:
                api.cmd_release_action(args, action)
        self.assertEqual(len(request.call_args_list), len(actions))
        for action, call in zip(actions, request.call_args_list):
            self.assertEqual(call.args[1], f"/v1/dev/releases/rel%2F1/{action}")
            self.assertEqual(call.kwargs["method"], "POST")
            self.assertEqual(call.kwargs["body"], {})
            self.assertTrue(call.kwargs["idempotency_key"].startswith(
                f"agentour-release-{action}-"))
        self.assertEqual(record.call_args_list[-1].kwargs["action"], "rollback")

    def test_release_rollback_can_bind_an_explicit_immutable_target(self):
        api = load_api()
        args = SimpleNamespace(platform="test", release_id="rel_current",
                               target_release_id="rel_previous")
        with mock.patch.object(api, "authenticated", return_value={
                "release_id": "rel_previous", "status": "published",
                "contract_version": "1.0"}) as request, \
             mock.patch.object(api, "record_flight"), mock.patch("builtins.print"):
            api.cmd_release_action(args, "rollback")
        self.assertEqual(request.call_args.kwargs["body"], {
            "target_release_id": "rel_previous"
        })
        self.assertTrue(request.call_args.kwargs["idempotency_key"].startswith(
            "agentour-release-rollback-"))

    def test_forge_status_commands_are_documented_and_visible_in_help(self):
        guide = (PLUGIN / "guides/forge-workflow.md").read_text(encoding="utf-8")
        skill = (PLUGIN / "skills/agentour-compiler/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("source-build-status <build-id>", guide)
        self.assertIn("source-eval-status <eval-run-id>", guide)
        self.assertNotIn("expose no corresponding GET route", guide)
        self.assertIn("`source-build-status`", skill)
        result = subprocess.run([
            sys.executable, str(PLUGIN / "scripts/agentour_api.py"), "--help"
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("source-build-status", result.stdout)
        self.assertIn("source-eval-status", result.stdout)
        for command in ("repositories", "repository-create", "repository-status",
                        "git-clone", "git-push"):
            self.assertIn(command, result.stdout)
        for command in ("release-submit-review", "release-approve", "release-activate",
                        "release-withdraw", "release-rollback"):
            self.assertIn(command, result.stdout)

    def test_token_guidance_requires_dedicated_api_token(self):
        guidance = "\n".join([
            (PLUGIN / "skills/agentour-compiler/SKILL.md").read_text(encoding="utf-8"),
            (PLUGIN / "guides/publishing.md").read_text(encoding="utf-8"),
        ])
        self.assertIn("dedicated `ak_...` API Token", guidance)
        self.assertIn("HttpOnly Cookie", guidance)
        self.assertIn("Never instruct the", guidance)
        self.assertIn("user to inspect browser storage", guidance)
        self.assertIn("never ask for or reuse browser cookies", guidance)

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
        self.assertIn("Mandatory reference-material gate", skill)
        self.assertIn("upload-references", skill)
        self.assertIn("Mandatory Feishu channel capability gate", skill)
        self.assertIn("channel_capabilities.feishu.skills", skill)
        self.assertIn("short-lived user credential", skill)
        self.assertIn("lark_cli_preflight.py", skill)
        self.assertIn("GitHub's latest", skill)
        self.assertIn("Mandatory interaction and approval policy choices",skill)
        self.assertIn("Mandatory runtime-efficiency contract",skill)
        reference = PLUGIN / "skills/agentour-compiler/references/feishu-capabilities.md"
        self.assertTrue(reference.is_file())
        self.assertIn("lark-task", reference.read_text(encoding="utf-8"))

    def test_lark_cli_preflight_accepts_only_matching_latest_versions(self):
        preflight = load_lark_preflight()
        with mock.patch.object(preflight, "github_latest", return_value="1.0.80"), \
             mock.patch.object(preflight, "npm_latest", return_value="1.0.80"), \
             mock.patch.object(preflight, "installed_version", return_value="1.0.80"), \
             mock.patch.object(preflight, "install_latest") as install, \
             mock.patch.object(preflight, "verify_skills", return_value=(
                 ["lark-task"], {"lark-task": "official contract"})):
            result = preflight.preflight(["lark-task"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["skill_contracts"]["lark-task"], "official contract")
        install.assert_not_called()

    def test_lark_cli_preflight_upgrades_missing_or_stale_cli(self):
        preflight = load_lark_preflight()
        with mock.patch.object(preflight, "github_latest", return_value="1.0.80"), \
             mock.patch.object(preflight, "npm_latest", return_value="1.0.80"), \
             mock.patch.object(preflight, "installed_version", side_effect=["1.0.79", "1.0.80"]), \
             mock.patch.object(preflight, "install_latest") as install, \
             mock.patch.object(preflight, "verify_skills", return_value=([], {})):
            result = preflight.preflight([])
        self.assertTrue(result["ready"])
        self.assertTrue(result["upgraded"])
        install.assert_called_once()

    def test_lark_cli_preflight_blocks_unverifiable_latest_version(self):
        preflight = load_lark_preflight()
        with mock.patch.object(preflight, "github_latest", return_value="1.0.80"), \
             mock.patch.object(preflight, "npm_latest", return_value="1.0.81"), \
             mock.patch.object(preflight, "install_latest") as install:
            result = preflight.preflight(["lark-task"])
        self.assertFalse(result["ready"])
        self.assertIn("disagree", result["error"])
        install.assert_not_called()

    def test_validator_rejects_agent_that_ends_on_missing_input(self):
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            (package / "payload/agent/instructions.md").write_text(
                "# Demo\n缺少信息调用 ask_question。工具失败时不得声称成功，并说明下一步。\n",
                encoding="utf-8",
            )
            result = subprocess.run([
                sys.executable, str(PLUGIN / "scripts/validate_package.py"), str(package)
            ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required-input state-machine", result.stdout)

    def test_validator_accepts_auto_execute_without_question_state_machine(self):
        with tempfile.TemporaryDirectory() as temp:
            package=pathlib.Path(temp)/"demo"
            self.make_package(package)
            manifest_path=package/"agentour.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["interaction_policy"]={"execution_mode":"auto_execute",
                "dangerous_action_approval":"none"}
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False),encoding="utf-8")
            (package/"payload/agent/instructions.md").write_text(
                "# Demo\n采用合理默认值，不要追问或调用 ask_question；在最终报告说明默认值。"
                "完全无法解析执行目标时诚实失败，不得编造。一次形成完整执行计划，Skill 和 Schema "
                "只加载一次并复用；同类操作批量执行，禁止每条记录单独触发模型。使用幂等键支持中断恢复。"
                "工具失败时不得声称成功，并说明下一步。\n",encoding="utf-8")
            result=subprocess.run([sys.executable,str(PLUGIN/"scripts/validate_package.py"),str(package)],
                capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_validator_enforces_agentour_managed_feishu_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            manifest_path=package/"agentour.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["channel_capabilities"]={"feishu": {
                "required": True, "skills": ["lark-doc"]}}
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False),encoding="utf-8")
            (package/"README.md").write_text(
                "# Demo\n使用前请在 Agentour 渠道中完成飞书授权并允许此 Agent。\n",
                encoding="utf-8")
            with (package/"payload/agent/instructions.md").open("a",encoding="utf-8") as file:
                file.write("\n飞书操作先调用 load_skill 读取 lark-doc，再按说明使用 lark-cli。\n")
            validator=PLUGIN/"scripts/validate_package.py"
            accepted=subprocess.run([sys.executable,str(validator),str(package)],
                                    capture_output=True,text=True)
            self.assertEqual(accepted.returncode,0,accepted.stdout+accepted.stderr)

            manifest["secrets"]=["FEISHU_APP_ID","FEISHU_APP_SECRET"]
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False),encoding="utf-8")
            rejected=subprocess.run([sys.executable,str(validator),str(package)],
                                    capture_output=True,text=True)
            self.assertNotEqual(rejected.returncode,0)
            self.assertIn("Agentour owns Feishu application credentials",rejected.stdout)

    def test_reference_upload_uses_developer_knowledge_endpoints(self):
        api = load_api()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "id": "ksi_1", "source_id": "ksrc_1"
        }).encode()
        with tempfile.TemporaryDirectory() as temp:
            reference = pathlib.Path(temp) / "expert.md"
            reference.write_text("# Expert", encoding="utf-8")
            args = SimpleNamespace(platform="test", files=[str(reference)])
            with mock.patch.dict(os.environ, {"AGENTOUR_TOKEN": "at_test"}), \
                 mock.patch.object(api.urllib.request, "urlopen", return_value=response) as upload, \
                 mock.patch.object(api, "authenticated", return_value={"assets": [{"id": "kas_1"}]}) as finalize, \
                 mock.patch("builtins.print"):
                api.cmd_upload_references(args)
        self.assertIn("/v1/dev/knowledge/sources/files", upload.call_args.args[0].full_url)
        self.assertEqual(finalize.call_args.args[1],
                         "/v1/dev/knowledge/sources/files/finalize-batch")

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
        self.assertFalse((PLUGIN / "templates/sandbox.ts").exists())
        skill = (PLUGIN / "skills/agentour-compiler/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("single-layer `agentour-e2b`", skill)

    def test_validator_rejects_package_authored_sandbox(self):
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            sandbox = package / "payload/agent/sandbox/sandbox.ts"
            sandbox.parent.mkdir(parents=True)
            sandbox.write_text("export default {};\n", encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(PLUGIN / "scripts/validate_package.py"), str(package)
            ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not author sandbox.ts", result.stdout)

    def test_token_requires_account_prefix(self):
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

    def test_tenant_subject_token_is_supported(self):
        api = load_api()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"developer_id":"tenant:ten_demo:subject:tsu_demo"}'
        with mock.patch.dict(os.environ, {"AGENTOUR_TOKEN": "ts_tenant_token"}), \
             mock.patch.object(api.urllib.request, "urlopen", return_value=response):
            result=api.request("test","/v1/dev/me",auth=True)
        self.assertTrue(result["developer_id"].startswith("tenant:"))

    def test_unified_account_token_prefix_is_accepted(self):
        api = load_api()
        with mock.patch.object(api, "get_token", return_value="ak_test"), \
             mock.patch.object(api.urllib.request, "urlopen") as urlopen:
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{}'
            urlopen.return_value = response
            self.assertEqual(api.request("test", "/v1/dev/me", auth=True), {})

    def test_flight_recorder_persists_redacted_job_evidence(self):
        script = PLUGIN / "scripts/flight_recorder.py"
        spec = importlib.util.spec_from_file_location("agentour_flight_test", script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("AGENTOUR_COMPILER_FLIGHT_LOG")
            os.environ["AGENTOUR_COMPILER_FLIGHT_LOG"] = str(pathlib.Path(td) / "flight.json")
            try:
                module.record("failure", error="Bearer secret-value ak_example123456789",
                              api_key="sk-secret-value",
                              tenant="ts_example123456789",
                              git="gcr_example.secret-value")
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
        self.assertNotIn("ak_example123456789", json.dumps(data))
        self.assertNotIn("ts_example123456789", json.dumps(data))
        self.assertNotIn("gcr_example.secret-value", json.dumps(data))
        self.assertEqual(data["events"][1]["poll_count"], 3)

    def test_forge_checkpoint_is_allowlisted_and_invalidates_changed_commit(self):
        api = load_api()
        checkpoint = {
            "repository_id": "repo_1",
            "commit_sha": "a" * 40,
            "remote_job_id": "bld_1",
            "contract_version": "1.0",
            "stage": "build_submitted",
        }
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "forge-checkpoint.json"
            self.assertEqual(api.write_forge_checkpoint(path, checkpoint), checkpoint)
            self.assertEqual(api.read_forge_checkpoint(path), checkpoint)
            changed = api.read_forge_checkpoint(path, "b" * 40)
        self.assertEqual(changed["commit_sha"], "b" * 40)
        self.assertEqual(changed["remote_job_id"], "")
        self.assertEqual(changed["stage"], "commit_changed")

    def test_forge_checkpoint_rejects_credentials_and_unknown_fields(self):
        api = load_api()
        checkpoint = {
            "repository_id": "repo_1", "commit_sha": "a" * 40,
            "remote_job_id": "", "contract_version": "1.0",
            "stage": "repository_resolved",
        }
        with self.assertRaises(ValueError):
            api.validate_forge_checkpoint({**checkpoint, "token": "ak_example123456789"})
        with self.assertRaises(ValueError):
            api.validate_forge_checkpoint({**checkpoint, "remote_job_id": "ak_example123456789"})
        with self.assertRaises(ValueError):
            api.validate_forge_checkpoint({**checkpoint, "remote_job_id": "ts_example123456789"})
        with self.assertRaises(ValueError):
            api.validate_forge_checkpoint({**checkpoint,
                                           "remote_job_id": "gcr_example.secret-value"})

    def test_publishing_docs_use_unified_token_and_valid_visibility_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (PLUGIN / "guides/publishing.md").read_text(encoding="utf-8")
        self.assertIn("`ak_` for a dedicated platform account API token", readme)
        self.assertIn("`ts_` for a tenant user", readme)
        self.assertNotIn("Enter a `at_` developer token", readme)
        self.assertIn("--visibility <private|public>", guide)

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
                module.set_token("test", "ak_test_token_value")
                module.set_token("production", "at_production_token_value")
                self.assertEqual(module.get_token("test"), "ak_test_token_value")
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
            if os.name == "nt":
                self.assertTrue(credential.is_file())
            else:
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
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
