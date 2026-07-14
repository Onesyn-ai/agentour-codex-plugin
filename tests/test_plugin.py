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


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "berth-compiler"


def load_api():
    path = PLUGIN / "scripts" / "berth_api.py"
    spec = importlib.util.spec_from_file_location("berth_api", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class PluginTests(unittest.TestCase):
    def make_package(self, root: pathlib.Path):
        files = {
            "README.md": "# Demo\n",
            "RELEASE.md": "# 0.1.0\n",
            "tests/smoke.yaml": "cases: []\n",
            "payload/package.json": "{}\n",
            "payload/pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            "payload/agent/agent.ts": "const url = process.env.BERTH_URL;\n",
            "payload/agent/instructions.md": "# Demo\n",
            "payload/agent/sandbox/sandbox.ts": "export default {};\n",
        }
        manifest = {
            "id": "demo", "name": "Demo", "version": "0.1.0", "runtime": "eve",
            "capabilities": ["review"], "description": "Demo", "pricing": {"model": "per_run", "amount_cents": 5},
            "runtime_ui": {
                "startup_message": "正在启动审查助手…",
                "default_working_message": "正在分析内容…",
                "capabilities": {"review": {"display_name": "内容审查", "loading_message": "正在加载内容审查能力…"}},
            },
        }
        files["berth.json"] = json.dumps(manifest, ensure_ascii=False)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_manifest_and_marketplace_names_match(self):
        manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
        market = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
        self.assertEqual(manifest["name"], "berth-compiler")
        self.assertEqual(market["plugins"][0]["name"], manifest["name"])

    def test_url_is_required(self):
        api = load_api()
        old = os.environ.pop("BERTH_URL", None)
        try:
            with self.assertRaises(SystemExit):
                api.base_url()
        finally:
            if old is not None:
                os.environ["BERTH_URL"] = old

    def test_package_tarball(self):
        api = load_api()
        with tempfile.TemporaryDirectory() as temp:
            package = pathlib.Path(temp) / "demo"
            self.make_package(package)
            payload = api.package_payload(package)
            self.assertEqual(payload[:2], b"\x1f\x8b")
            with tarfile.open(fileobj=__import__("io").BytesIO(payload), mode="r:gz") as archive:
                self.assertIn("demo/berth.json", archive.getnames())

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
            manifest_path = package / "berth.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["runtime_ui"]["capabilities"]["review"]["loading_message"] = "load skill review"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(PLUGIN / "scripts/validate_package.py"), str(package)
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Internal terminology", result.stdout)


if __name__ == "__main__":
    unittest.main()
