#!/usr/bin/env python3
"""Static preflight aligned with Agentour Compiler Contract v4."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import hashlib
import fnmatch

FORBIDDEN_UI = re.compile(r"\b(load skill|skill loaded|tool call|runtime boot|handler)\b", re.I)
SECRET_CONTENT = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
]
EXCLUDED = {"node_modules", ".output", ".eve", ".workflow-data", ".git", ".agentour", "__pycache__"}
ALLOWED_SMOKE = {"send", "expect_tool", "expect_contains", "expect_approval", "expect_question"}
TEMPLATE_PLACEHOLDER = re.compile(
    r"\b(?:AGENT_ID|AGENT_NAME|AGENT_DESCRIPTION|CAPABILITY_ID|CAPABILITY_DISPLAY_NAME|"
    r"MODEL_ID|ROLE_NAME|RESPONSIBILITY|OUTPUT_REQUIREMENTS|"
    r"DELIVERABLE_ACCEPTANCE_CRITERIA|COMPLETE_SELF_CONTAINED_INPUT|"
    r"INCOMPLETE_INPUT|EXPECTED_OUTPUT|EXAMPLE_[1-9]|AUTHOR_NAME|TAG_[1-9]|GREETING_TEXT)\b")
ENV_SECRET = re.compile(r"(?:process\.env\.|process\.env\[['\"])([A-Z][A-Z0-9_]{2,})")
PLATFORM_UNSUPPORTED_ROUTE = re.compile(
    r"(?:AGENTOUR_URL|agentourURL|agentour\.ai)[^\n]{0,200}/v1/(?:images/generations|audio/|video/)", re.I)
CONTEXT_DEPENDENT_SMOKE = re.compile(r"(?:上一(?:版|轮|个)|刚才|之前那个|继续(?:上次|刚才)|同上)")
LOCK_EXCLUDES = {"node_modules", ".output", ".eve", ".workflow-data", ".git",
                 ".agentour", "package.lock", "__pycache__"}


def generate_package_lock(root: pathlib.Path) -> dict:
    hashes = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (not path.is_file() or any(part in LOCK_EXCLUDES for part in rel.parts)
                or fnmatch.fnmatch(path.name, ".agentour-*.log")):
            continue
        hashes[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    tree = hashlib.sha256()
    for rel, digest in sorted(hashes.items()):
        tree.update(rel.encode()); tree.update(b"\0"); tree.update(digest.encode()); tree.update(b"\n")
    lock = {"version": 1, "hash": tree.hexdigest(), "files": hashes,
            "generated_by": "agentourcore.lockfile/1"}
    (root / "package.lock").write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return lock


def files(root: pathlib.Path):
    for path in root.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED for part in path.relative_to(root).parts):
            continue
        yield path


def validate_smoke(path: pathlib.Path) -> list[str]:
    problems = []
    current = None
    cases = []
    in_cases = False
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        text = raw.strip(); indent = len(raw) - len(raw.lstrip(" "))
        if text.startswith("schema_version:"):
            if text.partition(":")[2].strip() != "1": problems.append("Only smoke schema_version 1 is supported")
            continue
        if text == "cases:": in_cases = True; continue
        if not in_cases: problems.append(f"Smoke line {line_no}: expected cases:"); continue
        if indent == 2 and text.startswith("- "):
            current = {}; cases.append(current); text = text[2:]
        elif text.startswith("- "):
            problems.append(f"Smoke line {line_no}: nested lists are not supported"); continue
        if current is None or ":" not in text:
            problems.append(f"Smoke line {line_no}: invalid flat field"); continue
        key, _, value = text.partition(":"); key = key.strip()
        if key not in ALLOWED_SMOKE:
            problems.append(f"Smoke line {line_no}: unsupported field {key}")
        else:
            current[key] = value.strip()
    for i, case in enumerate(cases, 1):
        if not case.get("send"): problems.append(f"Smoke case #{i} missing send")
        if len(case) < 2: problems.append(f"Smoke case #{i} needs an expect_* assertion")
        if CONTEXT_DEPENDENT_SMOKE.search(str(case.get("send", ""))):
            problems.append(f"Smoke case #{i} depends on missing conversation history; schema v1 cases must be self-contained")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("package"); args = parser.parse_args()
    started = time.monotonic(); root = pathlib.Path(args.package).resolve()
    lock = generate_package_lock(root)
    critical: list[str] = []; warnings: list[str] = []; passed: list[str] = []
    required = ["agentour.json", "README.md", "RELEASE.md", "tests/smoke.yaml",
                "payload/package.json", "payload/pnpm-lock.yaml", "payload/agent/agent.ts",
                "payload/agent/instructions.md", "payload/agent/sandbox/sandbox.ts"]
    missing = [item for item in required if not (root / item).is_file()]
    critical.extend("Missing file: " + item for item in missing)

    try: manifest = json.loads((root / "agentour.json").read_text(encoding="utf-8"))
    except Exception as exc: manifest = {}; critical.append(f"Invalid agentour.json: {exc}")
    for key in ("id", "name", "version", "runtime", "capabilities", "description", "pricing"):
        if not manifest.get(key): critical.append(f"Manifest field is required: {key}")
    pricing = manifest.get("pricing") or {}
    if not isinstance(pricing.get("amount_credits"), int):
        critical.append("pricing.amount_credits must be an integer number of credits")
    if "amount_cents" in pricing:
        warnings.append("pricing.amount_cents is legacy; use amount_credits (credits, not RMB cents)")
    declared_secrets = manifest.get("secrets") or []
    if not isinstance(declared_secrets, list) or any(not isinstance(item, str) or not item.strip()
                                                     for item in declared_secrets):
        critical.append("manifest.secrets must be a list of environment-variable names")
        declared_secrets = []
    invalid_secret_names = [item for item in declared_secrets
                            if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", item)]
    if invalid_secret_names:
        critical.append("Invalid manifest secret names: " + ", ".join(invalid_secret_names))

    try:
        package_json = json.loads((root / "payload/package.json").read_text(encoding="utf-8"))
        if str((package_json.get("engines") or {}).get("node", "")) != ">=24":
            critical.append('payload/package.json must declare engines.node: ">=24"')
        if package_json.get("packageManager") != "pnpm@10.23.0":
            critical.append('payload/package.json must declare packageManager: "pnpm@10.23.0"')
        if int(manifest.get("compiler_contract_version", 0) or 0) >= 4:
            dependencies = {**(package_json.get("dependencies") or {}),
                            **(package_json.get("devDependencies") or {})}
            ranged = [f"{name}@{version}" for name, version in dependencies.items()
                      if isinstance(version, str) and version.startswith(("^", "~", ">", "<", "*"))]
            if ranged: critical.append("Contract v4 requires exact dependency versions: " + ", ".join(ranged))
            if "overrides" in (package_json.get("pnpm") or {}):
                critical.append("Contract v4 forbids package.json#pnpm.overrides; use explicit dependencies")
    except Exception as exc: critical.append(f"Invalid payload/package.json: {exc}")

    if int(manifest.get("compiler_contract_version", 0) or 0) >= 4:
        agent_source = ((root / "payload/agent/agent.ts").read_text(encoding="utf-8", errors="replace")
                        if (root / "payload/agent/agent.ts").is_file() else "")
        if re.search(r"defineAgent\s*\(\s*\{[\s\S]*?\bsystem\s*:", agent_source):
            critical.append("Eve 0.20 does not accept defineAgent.system; use agent/instructions.md")
        if re.search(r"if\s*\(\s*!\s*\w*(?:token|key|url)\w*\s*\)\s*\{?\s*throw\b",
                     agent_source, re.I):
            critical.append("Do not throw for missing Runtime credentials during module import/build")
        lock_text = ((root / "payload/pnpm-lock.yaml").read_text(encoding="utf-8", errors="replace")
                     if (root / "payload/pnpm-lock.yaml").is_file() else "")
        workspace = ((root / "payload/pnpm-workspace.yaml").read_text(encoding="utf-8", errors="replace")
                     if (root / "payload/pnpm-workspace.yaml").is_file() else "")
        release_age = re.search(r"(?m)^\s*minimumReleaseAge\s*:\s*(\d+)\s*$", workspace)
        if not release_age or int(release_age.group(1)) < 1440:
            critical.append("pnpm-workspace.yaml must set minimumReleaseAge: 1440")
        native = [name for name in ("@mongodb-js/zstd", "node-liblzma") if name in lock_text]
        missing_native = [name for name in native if not re.search(
            rf"(?m)^\s*['\"]?{re.escape(name)}['\"]?\s*:\s*true\s*$", workspace)]
        if native and ("allowBuilds:" not in workspace or missing_native):
            critical.append("pnpm-workspace.yaml allowBuilds is missing: " + ", ".join(missing_native or native))

    runtime_ui = manifest.get("runtime_ui") or {}; ui_caps = runtime_ui.get("capabilities") or {}
    for capability in manifest.get("capabilities") or []:
        item = ui_caps.get(capability)
        if not isinstance(item, dict): critical.append(f"Missing runtime_ui for capability: {capability}"); continue
        for field in ("display_name", "loading_message"):
            value = str(item.get(field, "")).strip()
            if not value: critical.append(f"runtime_ui.capabilities.{capability}.{field} is required")
            elif FORBIDDEN_UI.search(value): critical.append(f"Internal terminology in {capability}.{field}")
    for field in ("startup_message", "default_working_message"):
        value = str(runtime_ui.get(field, "")).strip()
        if not value: critical.append(f"runtime_ui.{field} is required")
        elif FORBIDDEN_UI.search(value): critical.append(f"Internal terminology in runtime_ui.{field}")

    if int(manifest.get("compiler_contract_version", 0) or 0) >= 3:
        tool_ux = manifest.get("tool_ux") or {}
        tools_dir = root / "payload/agent/tools"
        if tools_dir.is_dir():
            for tool_file in sorted(tools_dir.glob("*.ts")):
                name = tool_file.stem
                zh_name = str((tool_ux.get(name) or {}).get("zh_name", ""))
                if not re.search(r"[\u4e00-\u9fff]", zh_name):
                    critical.append(f"tool_ux.{name}.zh_name must contain a business-readable Chinese name")
        approval_ux = manifest.get("approval_ux") or {}
        approval_fields = {"title", "purpose", "action", "impact", "risk", "deny_effect"}
        for tool in manifest.get("approval_required") or []:
            contract = approval_ux.get(tool) if isinstance(approval_ux, dict) else None
            missing_fields = sorted(approval_fields - set(contract or {}))
            if missing_fields:
                critical.append(f"approval_ux.{tool} missing fields: {', '.join(missing_fields)}")
        deliverable = manifest.get("deliverable") or {}
        if deliverable.get("required") is not True:
            critical.append("deliverable.required must be true")
        if not deliverable.get("formats"):
            critical.append("deliverable.formats must declare at least one output format")
        examples = manifest.get("examples") or []
        if len(examples) < 2 or any(not str(item).strip() or "EXAMPLE_" in str(item) for item in examples):
            critical.append("manifest.examples must contain at least two complete, executable user inputs")

    scanned = 0
    referenced_secrets: set[str] = set()
    for path in files(root):
        scanned += 1
        if path.stat().st_size > 2_000_000: continue
        try: text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError): continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("payload/"):
            referenced_secrets.update(name for name in ENV_SECRET.findall(text)
                                      if not name.startswith("AGENTOUR_") and
                                      re.search(r"(?:TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL)", name))
        for pattern in SECRET_CONTENT:
            if pattern.search(text): critical.append(f"Possible credential content in {rel}"); break
        if rel == "agentour.json" and FORBIDDEN_UI.search(text): critical.append("agentour.json exposes internal terminology")
        placeholders = sorted(set(TEMPLATE_PLACEHOLDER.findall(text)))
        if placeholders:
            critical.append(f"Unresolved template placeholders in {rel}: {', '.join(placeholders)}")
        if PLATFORM_UNSUPPORTED_ROUTE.search(text):
            critical.append(f"{rel} calls an unsupported Agentour media route; use a discovered image model/tool or declare an external service")
    platform_env = {"AGENTOUR_URL", "AGENTOUR_RUNTIME_TOKEN", "PORT", "NODE_ENV", "HOME"}
    undeclared = sorted(referenced_secrets - set(declared_secrets) - platform_env)
    if undeclared:
        critical.append("Runtime environment variables used but not declared in manifest.secrets: " + ", ".join(undeclared))
    unused = sorted(set(declared_secrets) - referenced_secrets)
    if unused:
        warnings.append("Manifest secrets are declared but not referenced by payload code: " + ", ".join(unused))
    smoke = root / "tests/smoke.yaml"
    if smoke.is_file():
        critical.extend(validate_smoke(smoke))
        smoke_text = smoke.read_text(encoding="utf-8")
        expected_tools = set(re.findall(r"(?m)^\s*expect_tool:\s*['\"]?([^'\"\s#]+)", smoke_text))
        source_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace") for path in files(root)
            if path.suffix in {".ts", ".js", ".mjs", ".cjs"})
        tool_file_names = {path.stem for path in (root / "payload/agent/tools").glob("*.*")}
        missing_tools = sorted(tool for tool in expected_tools
                               if tool not in tool_file_names and
                               not re.search(rf"\b{re.escape(tool)}\b", source_text))
        if missing_tools:
            critical.append("Smoke expects tools not found in payload source: " + ", ".join(missing_tools))
    instructions = root / "payload/agent/instructions.md"
    content = instructions.read_text(encoding="utf-8") if instructions.is_file() else ""
    if "ask_question" not in content: warnings.append("Missing-input behavior should use Eve ask_question")
    if not re.search(r"(失败|error).{0,80}(不得声称成功|do not claim success|下一步)", content, re.I | re.S):
        critical.append("instructions.md must define honest tool-failure and fallback-delivery behavior")
    if manifest.get("approval_required") and "审批" not in content and "approval" not in content.lower():
        critical.append("Approval is declared but instructions do not explain it")
    if re.search(r"等待审批.{0,20}(正在执行|运行中|思考中)", content):
        critical.append("Waiting for approval is incorrectly described as running")

    try:
        version = subprocess.check_output(["node", "--version"], text=True).strip().lstrip("v")
        if int(version.split(".", 1)[0]) < 24: warnings.append(f"Local Node {version} is too old for Eve; use Node 24")
    except Exception: warnings.append("Node.js not found; local build is unverified")

    if not critical: passed.extend(["Required files and manifest passed", "Upload-scope secret scan passed",
                                    f"Generated package.lock {lock['hash'][:16]}"])
    for title, values in (("Critical", critical), ("Warnings", warnings), ("Passed", passed)):
        if values:
            print(title + ":")
            for item in sorted(set(values)): print(f"- {item}")
    print(f"Scanned {scanned} uploadable files in {time.monotonic() - started:.2f}s; excluded {sorted(EXCLUDED)}")
    return 1 if critical else 0


if __name__ == "__main__": sys.exit(main())
