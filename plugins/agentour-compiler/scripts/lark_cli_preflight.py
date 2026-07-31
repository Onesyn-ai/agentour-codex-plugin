#!/usr/bin/env python3
"""Strict development-time preflight for Feishu/Lark Agent compilation."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request


GITHUB_LATEST = "https://api.github.com/repos/larksuite/cli/releases/latest"
NPM_PACKAGE = "@larksuite/cli"


def normalize_version(value: str) -> str:
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)", str(value or ""))
    if not match:
        raise ValueError(f"cannot parse semantic version from {value!r}")
    return match.group(1)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def github_latest() -> str:
    request = urllib.request.Request(
        GITHUB_LATEST,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "agentour-compiler"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return normalize_version(json.loads(response.read().decode("utf-8"))["tag_name"])


def npm_latest() -> str:
    result = run(["npm", "view", NPM_PACKAGE, "version", "--json"])
    return normalize_version(json.loads(result.stdout))


def installed_version() -> str:
    binary = shutil.which("lark-cli")
    if not binary:
        return ""
    result = run([binary, "--version"])
    return normalize_version(result.stdout or result.stderr)


def install_latest() -> None:
    # The official installer also installs the Skills bundled with this exact CLI release.
    run(["npx", "-y", f"{NPM_PACKAGE}@latest", "install", "--help"])


def verify_skills(required: list[str]) -> tuple[list[str], dict[str, str]]:
    listing = run(["lark-cli", "skills", "list"]).stdout
    missing = [skill for skill in required if skill not in listing]
    if missing:
        raise RuntimeError("required official Skills are unavailable: " + ", ".join(missing))
    contracts: dict[str, str] = {}
    for skill in required:
        result = run(["lark-cli", "skills", "read", skill])
        if not result.stdout.strip():
            raise RuntimeError(f"official Skill contract is empty: {skill}")
        contracts[skill] = result.stdout
    return required, contracts


def preflight(required: list[str]) -> dict:
    result = {
        "ready": False,
        "installed_version": "",
        "github_latest": "",
        "npm_latest": "",
        "upgraded": False,
        "skills_available": [],
        "skill_contracts": {},
    }
    try:
        result["github_latest"] = github_latest()
        result["npm_latest"] = npm_latest()
        if result["github_latest"] != result["npm_latest"]:
            raise RuntimeError(
                "official latest versions disagree: GitHub "
                f"{result['github_latest']} vs npm {result['npm_latest']}"
            )
        result["installed_version"] = installed_version()
        if result["installed_version"] != result["github_latest"]:
            install_latest()
            result["upgraded"] = True
            result["installed_version"] = installed_version()
        if result["installed_version"] != result["github_latest"]:
            raise RuntimeError(
                f"lark-cli remains {result['installed_version'] or 'missing'} after upgrade; "
                f"required {result['github_latest']}"
            )
        skills, contracts = verify_skills(required)
        result["skills_available"] = skills
        result["skill_contracts"] = contracts
        result["ready"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", nargs="*", default=[])
    args = parser.parse_args()
    result = preflight(list(dict.fromkeys(args.skills)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
