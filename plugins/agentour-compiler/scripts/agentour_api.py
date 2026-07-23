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
import hashlib
import urllib.error
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from credential_store import delete_token, get_token
from flight_recorder import read as read_flight, record as record_flight, record_job_sample

PLATFORMS = {
    "test": {"name": "测试服", "url": "https://test.agentour.ai"},
    "production": {"name": "正式服", "url": "https://agentour.ai"},
}
DEFAULT_IGNORES = {
    "node_modules", ".output", ".eve", ".workflow-data", ".git",
    "__pycache__", ".DS_Store",
}
DEFAULT_PATTERNS = {"*.log", "*.tmp", "*.swp", ".agentour-*.log"}
def installed_plugin_version() -> str:
    """Read the manifest so release metadata cannot drift from the client."""
    manifest = pathlib.Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])
    except (OSError, KeyError, TypeError, ValueError):
        return "0.0.0"


PLUGIN_VERSION = installed_plugin_version()
LATEST_MANIFEST_URL = "https://raw.githubusercontent.com/Onesyn-ai/agentour-codex-plugin/main/plugins/agentour-compiler/.codex-plugin/plugin.json"


class APITransportError(RuntimeError):
    """A retryable transport failure. POST callers must not blindly resubmit."""


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
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise APITransportError(f"Cannot reach {base_url(platform)}: {reason}") from exc


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


def poll_job(args, path: str, job_type: str, job_id: str):
    """Poll an already-created job; transient reads never create a replacement job."""
    try:
        return authenticated(args, path)
    except APITransportError as exc:
        record_flight("job_poll_transport_error", job_type=job_type, job_id=job_id,
                      error=str(exc), retrying_same_job=True)
        print(json.dumps({"job_id": job_id, "status": "tracking_interrupted",
                          "retrying_same_job": True, "error": str(exc)},
                         ensure_ascii=False), flush=True)
        return None


def sync_flight(args, task_id: str = "") -> None:
    """Best-effort mirror of recent redacted events into the durable remote Compiler Task."""
    if not task_id:
        state_path = pathlib.Path(".agentour/compiler-state.json")
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                task_id = str(state.get("compiler_task_id") or state.get("task_id") or "")
            except Exception:
                task_id = ""
    if not task_id:
        return
    try:
        quoted = urllib.parse.quote(task_id, safe="")
        task = authenticated(args, f"/v1/dev/compiler-tasks/{quoted}")
        flight = read_flight()
        authenticated(args, f"/v1/dev/compiler-tasks/{quoted}", method="PATCH", body={
            "expected_revision": task.get("revision"),
            "state": {"flight_recorder": {
                "report_schema_version": flight.get("report_schema_version", "1.0"),
                "updated_at": flight.get("updated_at"),
                "event_count": len(flight.get("events", [])),
                "events": flight.get("events", [])[-100:],
            }},
        })
    except BaseException:
        # Telemetry persistence must never make an otherwise valid build/publish fail.
        return


def cmd_verify_token(args):
    result = authenticated(args, "/v1/dev/me")
    print(json.dumps({"valid": True, "platform": PLATFORMS[args.platform]["name"],
                      "developer_id": result.get("developer_id")}, ensure_ascii=False), flush=True)


def cmd_models(args):
    print(json.dumps(discover_models(args), ensure_ascii=False, indent=2), flush=True)


def cmd_check_update(args):
    result = check_update(auto=args.auto)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if result.get("outdated") and args.auto and not result.get("updated"):
        raise SystemExit(1)


def check_update(*, auto: bool) -> dict:
    try:
        with urllib.request.urlopen(LATEST_MANIFEST_URL, timeout=15) as response:
            latest = str(json.loads(response.read()).get("version", "")).split("+", 1)[0]
    except Exception as exc:
        return {"checked": False, "current": PLUGIN_VERSION,
                "warning": f"无法检查 Plugin 更新: {exc}"}
    current = PLUGIN_VERSION.split("+", 1)[0]
    def version_key(value):
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
        return tuple(map(int, match.groups())) if match else (0, 0, 0)
    outdated = version_key(latest) > version_key(current)
    result = {"checked": True, "current": current, "latest": latest,
              "outdated": outdated, "updated": False}
    if outdated and auto:
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
    return result


def discover_models(args) -> dict:
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
    return {"object": "list", "data": available,
            "recommended_model": available[0]["id"] if available else None,
            "filtered_unavailable": unavailable}


def cmd_bootstrap(args):
    update = check_update(auto=True)
    result = {"bootstrap_version": 1, "plugin_update": update,
              "ready_for_interview": False}
    if update.get("updated"):
        result["restart_required"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return
    if update.get("outdated") and not update.get("updated"):
        result["blocked"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit(1)
    platform = args.target_platform
    if not platform:
        state_path = pathlib.Path(".agentour/compiler-state.json")
        inferred = ""
        if state_path.is_file():
            try:
                raw = json.loads(state_path.read_text(encoding="utf-8"))
                inferred = str(raw.get("platform") or raw.get("platform_id") or "")
            except Exception:
                pass
        if inferred in PLATFORMS:
            platform = inferred
        else:
            result["platform_choice_required"] = True
            result["platforms"] = PLATFORMS
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return
    args.platform = platform
    token = os.environ.get("AGENTOUR_TOKEN", "").strip() or get_token(platform)
    if not token.startswith("at_"):
        result.update({"platform": platform, "token_required": True})
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return
    try:
        identity = authenticated(args, "/v1/dev/me")
        contract = authenticated(args, "/v1/dev/compiler-contract")
        models = discover_models(args)
        tasks = authenticated(args, "/v1/dev/compiler-tasks?active=true")
    except SystemExit as exc:
        result.update({"platform": platform, "blocked": True, "error": str(exc)})
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        raise
    result.update({
        "platform": platform,
        "developer_id": identity.get("developer_id"),
        "contract_version": contract.get("contract_version"),
        "contract": contract,
        "models": models,
        "active_compiler_tasks": tasks,
        "ready_for_interview": bool(models.get("recommended_model")),
    })
    record_flight("bootstrap_completed", platform=platform,
                  contract_version=contract.get("contract_version"),
                  developer_id=identity.get("developer_id"),
                  recommended_model=models.get("recommended_model"),
                  active_compiler_task_ids=[item.get("id") for item in tasks])
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if not result["ready_for_interview"]:
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
    record_flight("publish_submitted", platform=args.platform, visibility=args.visibility,
                  package=str(package), archive=stats, response=result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    job_id = result.get("job_id") if isinstance(result, dict) else None
    if not asynchronous or not job_id or args.no_wait:
        return
    deadline = time.monotonic() + args.timeout
    previous = None
    polls = 0; unchanged_since = time.monotonic(); last_sample_at = 0.0
    while time.monotonic() < deadline:
        polls += 1
        job = authenticated(args, f"/v1/dev/publish-jobs/{job_id}")
        signature = (job.get("status"), job.get("updated_at"), job.get("error"))
        changed = signature != previous
        if changed:
            unchanged_since = time.monotonic()
            print(json.dumps(job, ensure_ascii=False), flush=True)
            previous = signature
            sync_flight(args)
        if changed or time.monotonic() - last_sample_at >= 30:
            record_job_sample("publish", job, poll_count=polls,
                              unchanged_seconds=time.monotonic() - unchanged_since,
                              poll_interval_seconds=args.poll_interval)
            last_sample_at = time.monotonic()
        if job.get("status") in {"succeeded", "failed", "cancelled", "timed_out"}:
            sync_flight(args)
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
    pnpm_command = "pnpm.cmd" if os.name == "nt" else "pnpm"
    try:
        node_version = subprocess.check_output(["node", "--version"], text=True).strip()
        host_build = int(node_version.lstrip("v").split(".", 1)[0]) >= 24
        if host_build:
            subprocess.run([pnpm_command, "--version"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        host_build = False
    if not host_build:
        docker = subprocess.run(["docker", "image", "inspect", "agentour-runtime:1"],
                                text=True, capture_output=True)
        if docker.returncode != 0:
            raise SystemExit("Node 24+ is unavailable and Docker image agentour-runtime:1 is missing")
    td = tempfile.mkdtemp(prefix="agentour-build-")
    try:
        started_at = time.time()
        record_flight("local_build_started", package=str(package))
        target = pathlib.Path(td) / package.name
        names, patterns = ignore_rules(package)
        shutil_ignore = lambda _root, entries: [e for e in entries if e in names or any(fnmatch.fnmatch(e, p) for p in patterns)]
        import shutil
        shutil.copytree(package, target, ignore=shutil_ignore)
        work = target / "payload"
        docker_user = (["--user", f"{os.getuid()}:{os.getgid()}"]
                       if hasattr(os, "getuid") else [])
        commands = ([
            [pnpm_command, "install", "--frozen-lockfile"],
            [pnpm_command, "exec", "eve", "build"],
        ] if host_build else [[
            "docker", "run", "--rm", *docker_user,
            "-e", "HOME=/tmp", "-e", "AGENTOUR_BUILD=1",
            "-e", "AGENTOUR_URL=http://host.docker.internal:8600",
            "-e", "AGENTOUR_RUNTIME_TOKEN=build-only-placeholder",
            "-v", f"{work}:/agent", "-w", "/agent", "agentour-runtime:1",
            "sh", "-lc", "pnpm install --frozen-lockfile && pnpm exec eve build",
        ]])
        for command in commands:
            build_env = {**os.environ, "AGENTOUR_BUILD": "1",
                         "AGENTOUR_URL": "https://test.agentour.ai",
                         "AGENTOUR_RUNTIME_TOKEN": "build-only-placeholder"}
            result = subprocess.run(command, cwd=work, text=True, capture_output=True,
                                    timeout=args.timeout, env=build_env)
            if result.returncode != 0:
                record_flight("local_build_failed", package=str(package), command=command,
                              duration_seconds=round(time.time() - started_at, 3),
                              error=(result.stdout + result.stderr)[-4000:])
                raise SystemExit(f"{' '.join(command)} failed:\n{(result.stdout + result.stderr)[-4000:]}")
    finally:
        import shutil
        for attempt in range(4):
            try:
                shutil.rmtree(td)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                if attempt == 3:
                    record_flight("temporary_directory_cleanup_failed", path=td, error=str(exc))
                else:
                    time.sleep(0.2 * (attempt + 1))
    record_flight("local_build_completed", package=str(package),
                  duration_seconds=round(time.time() - started_at, 3))
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
    record_flight("validation_submitted", platform=args.platform, package=str(package),
                  archive=stats, response=result)
    job_id = result.get("job_id")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    deadline = time.monotonic() + args.timeout
    previous = None
    polls = 0; unchanged_since = time.monotonic(); last_sample_at = 0.0
    while time.monotonic() < deadline:
        polls += 1
        job = poll_job(args, f"/v1/dev/validate-jobs/{job_id}", "validation", job_id)
        if job is None:
            time.sleep(args.poll_interval)
            continue
        signature = (job.get("status"), job.get("updated_at"), job.get("error"))
        changed = signature != previous
        if changed:
            unchanged_since = time.monotonic()
            print(json.dumps(job, ensure_ascii=False), flush=True); previous = signature
            sync_flight(args)
        if changed or time.monotonic() - last_sample_at >= 30:
            record_job_sample("validation", job, poll_count=polls,
                              unchanged_seconds=time.monotonic() - unchanged_since,
                              poll_interval_seconds=args.poll_interval)
            last_sample_at = time.monotonic()
        if job.get("status") in {"succeeded", "failed", "cancelled", "timed_out"}:
            sync_flight(args)
            if job.get("status") != "succeeded": raise SystemExit(1)
            return
        time.sleep(args.poll_interval)
    raise SystemExit(f"Validation job {job_id} timed out")


def cmd_remote_build(args):
    package = pathlib.Path(args.package).resolve()
    payload, stats = package_payload(package)
    result = request(args.platform, "/v1/dev/builds", method="POST", data=payload,
                     auth=True, content_type="application/gzip")
    record_flight("remote_build_submitted", platform=args.platform, package=str(package),
                  archive=stats, response=result)
    print(json.dumps({**result, "archive": stats}, ensure_ascii=False), flush=True)
    job_id = result.get("job_id")
    if not job_id or args.no_wait:
        return
    deadline = time.monotonic() + args.timeout
    previous = None
    polls = 0; unchanged_since = time.monotonic(); last_sample_at = 0.0
    while time.monotonic() < deadline:
        polls += 1
        job = poll_job(args, f"/v1/dev/builds/{job_id}", "remote_build", job_id)
        if job is None:
            time.sleep(args.poll_interval)
            continue
        signature = (job.get("status"), json.dumps(job.get("data", {}).get("gates", []), sort_keys=True))
        changed = signature != previous
        if changed:
            unchanged_since = time.monotonic()
            print(json.dumps(job, ensure_ascii=False), flush=True); previous = signature
            sync_flight(args)
        if changed or time.monotonic() - last_sample_at >= 30:
            record_job_sample("remote_build", job, poll_count=polls,
                              unchanged_seconds=time.monotonic() - unchanged_since,
                              poll_interval_seconds=args.poll_interval)
            last_sample_at = time.monotonic()
        if job.get("status") in {"succeeded", "failed", "cancelled", "timed_out"}:
            sync_flight(args)
            if job.get("status") != "succeeded": raise SystemExit(1)
            return
        time.sleep(args.poll_interval)
    raise SystemExit(f"Build Job {job_id} timed out")


def cmd_cancel_build(args):
    result = authenticated(args, f"/v1/dev/builds/{urllib.parse.quote(args.job_id, safe='')}/cancel",
                           method="POST")
    print(json.dumps(result, ensure_ascii=False), flush=True)


def cmd_track_build(args):
    """Resume observation of an existing paid Build without submitting another archive."""
    deadline = time.monotonic() + args.timeout
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        job = poll_job(args, f"/v1/dev/builds/{urllib.parse.quote(args.job_id, safe='')}",
                       "remote_build", args.job_id)
        if job is None:
            time.sleep(args.poll_interval); continue
        print(json.dumps(job, ensure_ascii=False), flush=True)
        record_job_sample("remote_build", job, poll_count=polls, unchanged_seconds=0,
                          poll_interval_seconds=args.poll_interval)
        if job.get("status") in {"succeeded", "failed", "cancelled", "timed_out"}:
            if job.get("status") != "succeeded": raise SystemExit(1)
            return
        time.sleep(args.poll_interval)
    raise SystemExit(f"Build Job {args.job_id} is still non-terminal; resume with track-build {args.job_id}")


def cmd_compiler_tasks(args):
    suffix = "?active=true" if args.active else "?active=false"
    print(json.dumps(authenticated(args, "/v1/dev/compiler-tasks" + suffix),
                     ensure_ascii=False, indent=2), flush=True)


def cmd_create_compiler_task(args):
    state = json.loads(args.state) if args.state else {}
    body = {"operation": args.operation, "agent_id": args.agent_id,
            "platform": args.platform, "workspace_id": args.workspace_id,
            "state": state}
    result = authenticated(args, "/v1/dev/compiler-tasks", method="POST", body=body)
    record_flight("compiler_task_created", task=result)
    sync_flight(args, str(result.get("id", "")))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def cmd_update_compiler_task(args):
    body = {"state": json.loads(args.state) if args.state else {}}
    for key in ("stage", "status", "package_hash", "expected_revision"):
        value = getattr(args, key, None)
        if value is not None:
            body[key] = value
    task_id = urllib.parse.quote(args.task_id, safe="")
    result = authenticated(args, f"/v1/dev/compiler-tasks/{task_id}", method="PATCH", body=body)
    record_flight("compiler_task_updated", task=result, patch=body)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def cmd_checkpoint_package(args):
    package = pathlib.Path(args.package).resolve()
    payload, stats = package_payload(package)
    task_id = urllib.parse.quote(args.task_id, safe="")
    result = request(args.platform, f"/v1/dev/compiler-tasks/{task_id}/package",
                     method="POST", data=payload, auth=True,
                     content_type="application/gzip")
    record_flight("package_checkpoint_uploaded", task_id=args.task_id,
                  package=str(package), package_hash=result.get("package_hash"), archive=stats)
    sync_flight(args, args.task_id)
    print(json.dumps({**result, "archive": stats,
                      "local_sha256": hashlib.sha256(payload).hexdigest()},
                     ensure_ascii=False, indent=2), flush=True)


def cmd_restore_checkpoint(args):
    task_id = urllib.parse.quote(args.task_id, safe="")
    headers = {"Accept": "application/gzip"}
    token = os.environ.get("AGENTOUR_TOKEN", "").strip() or get_token(args.platform)
    if not token.startswith("at_"):
        raise SystemExit(f"No saved developer token for {args.platform}")
    headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base_url(args.platform) +
                                 f"/v1/dev/compiler-tasks/{task_id}/package", headers=headers)
    destination = pathlib.Path(args.destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = response.read()
            expected = response.headers.get("X-Package-SHA256", "")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Agentour API {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if expected and actual != expected:
        raise SystemExit("Checkpoint Package SHA-256 mismatch")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        root = destination.resolve()
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(root):
                raise SystemExit("Unsafe path in checkpoint archive")
        archive.extractall(destination)
    print(json.dumps({"restored": True, "destination": str(destination),
                      "sha256": actual}, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=PLATFORMS, default="production")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("platforms")
    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--target-platform", choices=PLATFORMS)
    sub.add_parser("verify-token")
    sub.add_parser("models")
    update = sub.add_parser("check-update")
    update.add_argument("--auto", action="store_true")
    sub.add_parser("contract")
    sub.add_parser("build-preflight")
    probe = sub.add_parser("model-probe")
    probe.add_argument("model")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("markdown")
    feedback.add_argument("--plugin-version", default="")
    feedback.add_argument("--operation", choices=("create", "reconstruct", "update"), required=True)
    feedback.add_argument("--agent-id", action="append", default=[])
    feedback.add_argument("--publish-job", default="")
    build_test = sub.add_parser("build-test")
    build_test.add_argument("package")
    build_test.add_argument("--timeout", type=float, default=900)
    validate = sub.add_parser("validate-package")
    validate.add_argument("package")
    validate.add_argument("--timeout", type=float, default=1800)
    validate.add_argument("--poll-interval", type=float, default=2)
    remote_build = sub.add_parser("remote-build")
    remote_build.add_argument("package")
    remote_build.add_argument("--no-wait", action="store_true")
    remote_build.add_argument("--timeout", type=float, default=1800)
    remote_build.add_argument("--poll-interval", type=float, default=2)
    cancel_build = sub.add_parser("cancel-build")
    cancel_build.add_argument("job_id")
    track_build = sub.add_parser("track-build")
    track_build.add_argument("job_id")
    track_build.add_argument("--timeout", type=float, default=1800)
    track_build.add_argument("--poll-interval", type=float, default=2)
    tasks = sub.add_parser("compiler-tasks")
    tasks.add_argument("--active", action=argparse.BooleanOptionalAction, default=True)
    create_task = sub.add_parser("create-compiler-task")
    create_task.add_argument("--operation", choices=("create", "reconstruct", "update"), required=True)
    create_task.add_argument("--agent-id", default="")
    create_task.add_argument("--workspace-id", required=True)
    create_task.add_argument("--state", default="{}")
    update_task = sub.add_parser("update-compiler-task")
    update_task.add_argument("task_id")
    update_task.add_argument("--stage")
    update_task.add_argument("--status", choices=("active", "blocked", "completed", "cancelled"))
    update_task.add_argument("--state", default="{}")
    update_task.add_argument("--package-hash")
    update_task.add_argument("--expected-revision", type=int)
    checkpoint = sub.add_parser("checkpoint-package")
    checkpoint.add_argument("task_id")
    checkpoint.add_argument("package")
    restore = sub.add_parser("restore-checkpoint")
    restore.add_argument("task_id")
    restore.add_argument("destination")
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
    elif args.command == "bootstrap":
        cmd_bootstrap(args)
    elif args.command == "verify-token":
        cmd_verify_token(args)
    elif args.command == "models":
        cmd_models(args)
    elif args.command == "check-update":
        cmd_check_update(args)
    elif args.command == "contract":
        print(json.dumps(authenticated(args, "/v1/dev/compiler-contract"), ensure_ascii=False, indent=2))
    elif args.command == "build-preflight":
        result = authenticated(args, "/v1/dev/build-preflight")
        record_flight("build_preflight", platform=args.platform, result=result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result.get("ready"):
            raise SystemExit(1)
    elif args.command == "model-probe":
        model = urllib.parse.quote(args.model, safe="")
        result = authenticated(args, f"/v1/dev/model-probe/{model}", method="POST")
        record_flight("model_probe", requested_model=args.model, result=result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "feedback":
        markdown = pathlib.Path(args.markdown).read_text(encoding="utf-8")
        body = {"plugin": "codex", "plugin_version": args.plugin_version,
                "operation": args.operation, "agent_ids": args.agent_id,
                "publish_job_id": args.publish_job, "markdown": markdown}
        result = authenticated(args, "/v1/dev/feedback", method="POST", body=body)
        record_flight("feedback_uploaded", filename=pathlib.Path(args.markdown).name,
                      feedback_id=result.get("feedback_id"), publish_job_id=args.publish_job)
        sync_flight(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "build-test":
        cmd_build_test(args)
    elif args.command == "validate-package":
        cmd_validate(args)
    elif args.command == "remote-build":
        cmd_remote_build(args)
    elif args.command == "cancel-build":
        cmd_cancel_build(args)
    elif args.command == "track-build":
        cmd_track_build(args)
    elif args.command == "compiler-tasks":
        cmd_compiler_tasks(args)
    elif args.command == "create-compiler-task":
        cmd_create_compiler_task(args)
    elif args.command == "update-compiler-task":
        cmd_update_compiler_task(args)
    elif args.command == "checkpoint-package":
        cmd_checkpoint_package(args)
    elif args.command == "restore-checkpoint":
        cmd_restore_checkpoint(args)
    elif args.command == "publish":
        cmd_publish(args, False)
    else:
        cmd_publish(args, True)


if __name__ == "__main__":
    main()
