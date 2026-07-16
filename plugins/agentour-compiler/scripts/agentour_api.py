#!/usr/bin/env python3
"""Agentour compiler API client: contract, probes, clean archives, jobs, and feedback."""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from credential_store import delete_token, get_token

PLATFORMS = {
    "local": {"name": "本地服", "url": "http://127.0.0.1:8600"},
    "competition": {"name": "比赛服", "url": "http://61.29.254.146"},
}
DEFAULT_IGNORES = {
    "node_modules", ".output", ".eve", ".workflow-data", ".git",
    "__pycache__", ".DS_Store",
}
DEFAULT_PATTERNS = {"*.log", "*.tmp", "*.swp", ".agentour-*.log"}
PLUGIN_VERSION = "0.5.1"
LATEST_MANIFEST_URL = "https://raw.githubusercontent.com/Onesyn-ai/agentour-codex-plugin/main/plugins/agentour-compiler/.codex-plugin/plugin.json"


def base_url(platform: str) -> str:
    return PLATFORMS[platform]["url"]


def request(platform: str, path: str, *, method: str = "GET",
            data: bytes | None = None, auth: bool = False,
            content_type: str = "application/json"):
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = content_type
    if auth:
        token = os.environ.get("AGENTOUR_TOKEN", "").strip() or get_token(platform)
        if not token.startswith("at_"):
            raise SystemExit(f"No saved developer token for {platform}; store one before continuing")
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base_url(platform) + path, data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if auth and exc.code in {401, 403} and not os.environ.get("AGENTOUR_TOKEN", "").strip():
            delete_token(platform)
        raise SystemExit(f"Agentour API {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach {base_url(platform)}: {exc.reason}") from exc


def ignore_rules(package_dir: pathlib.Path) -> tuple[set[str], set[str]]:
    names = set(DEFAULT_IGNORES)
    patterns = set(DEFAULT_PATTERNS)
    path = package_dir / ".agentourignore"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            rule = raw.strip().strip("/")
            if not rule or rule.startswith("#"):
                continue
            (patterns if any(c in rule for c in "*?[") else names).add(rule)
    return names, patterns


def package_files(package_dir: pathlib.Path):
    names, patterns = ignore_rules(package_dir)
    for root, dirs, files in os.walk(package_dir):
        dirs[:] = sorted(d for d in dirs if d not in names and
                         not any(fnmatch.fnmatch(d, p) for p in patterns))
        for name in sorted(files):
            if name in names or any(fnmatch.fnmatch(name, p) for p in patterns):
                continue
            path = pathlib.Path(root) / name
            yield path, path.relative_to(package_dir)


def package_payload(package_dir: pathlib.Path) -> tuple[bytes, dict]:
    files = list(package_files(package_dir))
    total = sum(path.stat().st_size for path, _ in files)
    largest = sorted(((path.stat().st_size, rel.as_posix()) for path, rel in files),
                     reverse=True)[:5]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, rel in files:
            archive.add(path, arcname=f"{package_dir.name}/{rel.as_posix()}", recursive=False)
    payload = buffer.getvalue()
    return payload, {"files": len(files), "source_bytes": total,
                     "archive_bytes": len(payload), "largest": largest}


def authenticated(args, path: str, *, method: str = "GET", body: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    return request(args.platform, path, method=method, data=data, auth=True)


def cmd_verify_token(args):
    result = authenticated(args, "/v1/dev/me")
    print(json.dumps({"valid": True, "platform": PLATFORMS[args.platform]["name"],
                      "developer_id": result.get("developer_id")}, ensure_ascii=False), flush=True)


def cmd_models(args):
    discovered = request(args.platform, "/v1/models?modality=chat").get("data", [])
    available, unavailable = [], []
    for item in discovered:
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        try:
            result = authenticated(
                args, f"/v1/dev/model-probe/{urllib.parse.quote(model_id, safe='')}",
                method="POST")
            if result.get("ok"):
                available.append({**item, "availability": "available",
                                  "probe": {"elapsed_seconds": result.get("elapsed_seconds")}})
            else:
                unavailable.append({"id": model_id, "error": result.get("error", "probe failed")})
        except SystemExit as exc:
            unavailable.append({"id": model_id, "error": str(exc)[:500]})
    available.sort(key=lambda item: (-int(item.get("quality_rank", 0)), item.get("id", "")))
    recommended = available[0]["id"] if available else None
    print(json.dumps({"object": "list", "data": available,
                      "recommended_model": recommended,
                      "filtered_unavailable": unavailable}, ensure_ascii=False, indent=2), flush=True)


def cmd_check_update(args):
    try:
        with urllib.request.urlopen(LATEST_MANIFEST_URL, timeout=15) as response:
            latest = str(json.loads(response.read()).get("version", "")).split("+", 1)[0]
    except Exception as exc:
        print(json.dumps({"checked": False, "current": PLUGIN_VERSION,
                          "warning": f"无法检查 Plugin 更新: {exc}"}, ensure_ascii=False), flush=True)
        return
    current = PLUGIN_VERSION.split("+", 1)[0]
    def version_key(value):
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
        return tuple(map(int, match.groups())) if match else (0, 0, 0)
    outdated = version_key(latest) > version_key(current)
    result = {"checked": True, "current": current, "latest": latest,
              "outdated": outdated, "updated": False}
    if outdated and args.auto:
        refresh = subprocess.run(
            ["codex", "plugin", "marketplace", "upgrade", "agentour-platform"],
            text=True, capture_output=True)
        completed = (subprocess.run(
            ["codex", "plugin", "add", "agentour-compiler@agentour-platform"],
            text=True, capture_output=True) if refresh.returncode == 0 else refresh)
        result["updated"] = refresh.returncode == 0 and completed.returncode == 0
        if not result["updated"]:
            result["error"] = (completed.stderr or completed.stdout)[-1000:]
        else:
            result["restart_required"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if outdated and args.auto and not result["updated"]:
        raise SystemExit(1)


def cmd_publish(args, asynchronous: bool):
    package = pathlib.Path(args.package).resolve()
    if not (package / "agentour.json").is_file():
        raise SystemExit(f"Missing agentour.json in {package}")
    contract = authenticated(args, "/v1/dev/compiler-contract")
    payload, stats = package_payload(package)
    max_mb = int(contract["package"]["upload_max_mb"])
    print(json.dumps({"archive": stats, "limit_mb": max_mb}, ensure_ascii=False), flush=True)
    if len(payload) > max_mb * 1024 * 1024:
        raise SystemExit(f"Clean archive is {len(payload) / 1024 / 1024:.1f}MB; limit is {max_mb}MB")
    query = urllib.parse.urlencode({"visibility": args.visibility})
    endpoint = ("/v1/dev/publish-async" if asynchronous else "/v1/dev/publish") + "?" + query
    result = request(args.platform, endpoint, method="POST", data=payload, auth=True,
                     content_type="application/gzip")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    job_id = result.get("job_id") if isinstance(result, dict) else None
    if not asynchronous or not job_id or args.no_wait:
        return
    deadline = time.monotonic() + args.timeout
    previous = None
    while time.monotonic() < deadline:
        job = authenticated(args, f"/v1/dev/publish-jobs/{job_id}")
        signature = (job.get("status"), job.get("updated_at"), job.get("error"))
        if signature != previous:
            print(json.dumps(job, ensure_ascii=False), flush=True)
            previous = signature
        if job.get("status") in {"succeeded", "failed", "cancelled", "timed_out"}:
            if job.get("status") != "succeeded":
                raise SystemExit(1)
            return
        time.sleep(args.poll_interval)
    raise SystemExit(f"Publish job {job_id} had no terminal result within {args.timeout}s")


def cmd_build_test(args):
    package = pathlib.Path(args.package).resolve()
    payload = package / "payload"
    if not (payload / "package.json").is_file():
        raise SystemExit(f"Missing payload/package.json in {package}")
    host_build = False
    try:
        node_version = subprocess.check_output(["node", "--version"], text=True).strip()
        host_build = int(node_version.lstrip("v").split(".", 1)[0]) >= 24
        if host_build:
            subprocess.run(["pnpm", "--version"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        host_build = False
    if not host_build:
        docker = subprocess.run(["docker", "image", "inspect", "agentour-runtime:1"],
                                text=True, capture_output=True)
        if docker.returncode != 0:
            raise SystemExit("Node 24+ is unavailable and Docker image agentour-runtime:1 is missing")
    with tempfile.TemporaryDirectory(prefix="agentour-build-") as td:
        target = pathlib.Path(td) / package.name
        names, patterns = ignore_rules(package)
        shutil_ignore = lambda _root, entries: [e for e in entries if e in names or any(fnmatch.fnmatch(e, p) for p in patterns)]
        import shutil
        shutil.copytree(package, target, ignore=shutil_ignore)
        work = target / "payload"
        docker_user = (["--user", f"{os.getuid()}:{os.getgid()}"]
                       if hasattr(os, "getuid") else [])
        commands = ([
            ["pnpm", "install", "--frozen-lockfile"],
            ["pnpm", "exec", "eve", "build"],
        ] if host_build else [[
            "docker", "run", "--rm", *docker_user,
            "-e", "HOME=/tmp", "-e", "AGENTOUR_BUILD=1",
            "-e", "AGENTOUR_URL=http://host.docker.internal:8600",
            "-v", f"{work}:/agent", "-w", "/agent", "agentour-runtime:1",
            "sh", "-lc", "pnpm install --frozen-lockfile && pnpm exec eve build",
        ]])
        for command in commands:
            result = subprocess.run(command, cwd=work, text=True, capture_output=True, timeout=args.timeout)
            if result.returncode != 0:
                raise SystemExit(f"{' '.join(command)} failed:\n{(result.stdout + result.stderr)[-4000:]}")
    print(json.dumps({"ok": True, "package": str(package),
                      "checks": ["pnpm install --frozen-lockfile", "pnpm exec eve build"]},
                     ensure_ascii=False), flush=True)


def cmd_validate(args):
    package = pathlib.Path(args.package).resolve()
    contract = authenticated(args, "/v1/dev/compiler-contract")
    payload, stats = package_payload(package)
    max_mb = int(contract["package"]["upload_max_mb"])
    if len(payload) > max_mb * 1024 * 1024:
        raise SystemExit(f"Clean archive exceeds {max_mb}MB")
    result = request(args.platform, "/v1/dev/validate-package", method="POST",
                     data=payload, auth=True, content_type="application/gzip")
    job_id = result.get("job_id")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    deadline = time.monotonic() + args.timeout
    previous = None
    while time.monotonic() < deadline:
        job = authenticated(args, f"/v1/dev/validate-jobs/{job_id}")
        signature = (job.get("status"), job.get("updated_at"), job.get("error"))
        if signature != previous:
            print(json.dumps(job, ensure_ascii=False), flush=True); previous = signature
        if job.get("status") in {"succeeded", "failed", "cancelled", "timed_out"}:
            if job.get("status") != "succeeded": raise SystemExit(1)
            return
        time.sleep(args.poll_interval)
    raise SystemExit(f"Validation job {job_id} timed out")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=PLATFORMS, default="competition")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("platforms")
    sub.add_parser("verify-token")
    sub.add_parser("models")
    update = sub.add_parser("check-update")
    update.add_argument("--auto", action="store_true")
    sub.add_parser("contract")
    probe = sub.add_parser("model-probe")
    probe.add_argument("model")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("markdown")
    feedback.add_argument("--plugin-version", default="")
    feedback.add_argument("--operation", choices=("create", "reconstruct"), required=True)
    feedback.add_argument("--agent-id", action="append", default=[])
    feedback.add_argument("--publish-job", default="")
    build_test = sub.add_parser("build-test")
    build_test.add_argument("package")
    build_test.add_argument("--timeout", type=float, default=900)
    validate = sub.add_parser("validate-package")
    validate.add_argument("package")
    validate.add_argument("--timeout", type=float, default=1800)
    validate.add_argument("--poll-interval", type=float, default=2)
    for name in ("publish", "publish-async"):
        publish = sub.add_parser(name)
        publish.add_argument("package")
        publish.add_argument("--visibility", choices=("private", "public"), required=True)
        if name == "publish-async":
            publish.add_argument("--no-wait", action="store_true")
            publish.add_argument("--timeout", type=float, default=1800)
            publish.add_argument("--poll-interval", type=float, default=2)
    args = parser.parse_args()
    if args.command == "platforms":
        print(json.dumps(PLATFORMS, ensure_ascii=False, indent=2))
    elif args.command == "verify-token":
        cmd_verify_token(args)
    elif args.command == "models":
        cmd_models(args)
    elif args.command == "check-update":
        cmd_check_update(args)
    elif args.command == "contract":
        print(json.dumps(authenticated(args, "/v1/dev/compiler-contract"), ensure_ascii=False, indent=2))
    elif args.command == "model-probe":
        model = urllib.parse.quote(args.model, safe="")
        print(json.dumps(authenticated(args, f"/v1/dev/model-probe/{model}", method="POST"),
                         ensure_ascii=False, indent=2))
    elif args.command == "feedback":
        markdown = pathlib.Path(args.markdown).read_text(encoding="utf-8")
        body = {"plugin": "codex", "plugin_version": args.plugin_version,
                "operation": args.operation, "agent_ids": args.agent_id,
                "publish_job_id": args.publish_job, "markdown": markdown}
        print(json.dumps(authenticated(args, "/v1/dev/feedback", method="POST", body=body),
                         ensure_ascii=False, indent=2))
    elif args.command == "build-test":
        cmd_build_test(args)
    elif args.command == "validate-package":
        cmd_validate(args)
    elif args.command == "publish":
        cmd_publish(args, False)
    else:
        cmd_publish(args, True)


if __name__ == "__main__":
    main()
