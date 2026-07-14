#!/usr/bin/env python3
"""Static preflight validation for a Berth Package."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


FORBIDDEN_UI = re.compile(r"\b(load skill|skill loaded|tool call|runtime boot|handler)\b", re.I)
SECRET_NAMES = re.compile(r"(^|[._-])(\.env|id_rsa|credentials?|secrets?)([._-]|$)", re.I)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    args = parser.parse_args()
    root = pathlib.Path(args.package).resolve()
    critical: list[str] = []
    warnings: list[str] = []
    passed: list[str] = []

    required = [
        "berth.json", "README.md", "RELEASE.md", "tests/smoke.yaml",
        "payload/package.json", "payload/pnpm-lock.yaml", "payload/agent/agent.ts",
        "payload/agent/instructions.md", "payload/agent/sandbox/sandbox.ts",
    ]
    missing = [item for item in required if not (root / item).is_file()]
    if missing:
        critical.append("Missing files: " + ", ".join(missing))
    else:
        passed.append("Required package files exist")

    manifest = {}
    try:
        manifest = json.loads((root / "berth.json").read_text(encoding="utf-8"))
    except Exception as exc:
        critical.append(f"Invalid berth.json: {exc}")

    for key in ("id", "name", "version", "runtime", "capabilities", "description", "pricing"):
        if not manifest.get(key):
            critical.append(f"Manifest field is required: {key}")

    runtime_ui = manifest.get("runtime_ui") or {}
    ui_caps = runtime_ui.get("capabilities") or {}
    for capability in manifest.get("capabilities") or []:
        item = ui_caps.get(capability)
        if not isinstance(item, dict):
            critical.append(f"Missing runtime_ui for capability: {capability}")
            continue
        for field in ("display_name", "loading_message"):
            value = str(item.get(field, "")).strip()
            if not value:
                critical.append(f"runtime_ui.capabilities.{capability}.{field} is required")
            elif FORBIDDEN_UI.search(value):
                critical.append(f"Internal terminology in user-facing field: {capability}.{field}")
            elif len(value) > 60:
                warnings.append(f"Long user-facing status: {capability}.{field}")

    for field in ("startup_message", "default_working_message"):
        value = str(runtime_ui.get(field, "")).strip()
        if not value:
            critical.append(f"runtime_ui.{field} is required")
        elif FORBIDDEN_UI.search(value):
            critical.append(f"Internal terminology in runtime_ui.{field}")

    text_files = [p for p in root.rglob("*") if p.is_file() and p.stat().st_size < 2_000_000]
    for path in text_files:
        rel = path.relative_to(root).as_posix()
        if SECRET_NAMES.search(path.name):
            critical.append(f"Possible secret file included: {rel}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "http://127.0.0.1:8600" in text or "http://localhost:8600" in text:
            critical.append(f"Forbidden localhost Berth fallback in {rel}")
        if rel.endswith("berth.json") and FORBIDDEN_UI.search(text):
            critical.append("berth.json exposes internal terminology")

    if manifest.get("approval_required"):
        instructions = (root / "payload/agent/instructions.md")
        content = instructions.read_text(encoding="utf-8") if instructions.is_file() else ""
        if "审批" not in content and "approval" not in content.lower():
            critical.append("Approval is declared but instructions do not explain approval behavior")
        if re.search(r"等待审批.{0,20}(正在执行|运行中|思考中)", content):
            critical.append("Waiting for approval is incorrectly described as running")

    if critical:
        print("Critical:")
        for item in sorted(set(critical)):
            print(f"- {item}")
    if warnings:
        print("Warnings:")
        for item in sorted(set(warnings)):
            print(f"- {item}")
    if passed:
        print("Passed:")
        for item in passed:
            print(f"- {item}")
    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
