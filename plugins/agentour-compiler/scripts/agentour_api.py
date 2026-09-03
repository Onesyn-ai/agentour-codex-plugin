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
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import hashlib
import gc
import urllib.error
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from credential_store import delete_credentials,get_credentials
from oauth_client import OAuthClientError,access_token,switch_account
from flight_recorder import read as read_flight, record as record_flight, record_job_sample

PLATFORMS = {
    "test": {"name": "测试服", "url": "https://test.agentour.ai"},
    "production": {"name": "正式服", "url": "https://agentour.ai"},
}
DEFAULT_IGNORES = {
    "node_modules", ".output", ".eve", ".workflow-data", ".git",
    ".agentour", "__pycache__", ".DS_Store",
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
FORGE_CONTRACT_VERSION = "1.0"
FORGE_CHECKPOINT_FIELDS = frozenset({
    "repository_id", "commit_sha", "remote_job_id", "contract_version", "stage",
})
FORGE_CHECKPOINT_STAGES = frozenset({
    "repository_resolved", "baseline_cloned", "changes_committed", "pushed",
    "review_pending", "source_revision_created", "build_submitted",
    "eval_submitted", "release_submitted", "release_review_submitted",
    "release_approved", "release_activated", "release_withdrawn",
    "release_rolled_back", "completed", "commit_changed",
})
_CHECKPOINT_SECRET = re.compile(
    r"(?:\b(?:ak|at|ts|e2b|gcr|ghp)_[A-Za-z0-9._-]{8,}\b|"
    r"\bgithub_pat_[A-Za-z0-9_]{8,}\b|\bglpat-[A-Za-z0-9_-]{8,}\b|"
    r"\bsk-[A-Za-z0-9_-]{8,}\b|Bearer\s+[^\s\"',}]+|"
    r"https?://[^\s/@:]+:[^\s/@]+@)", re.I)
_SECRET_KEY = re.compile(
    r"(?:token|secret|password|api[_-]?key|credential|authorization)", re.I)
_FULL_COMMIT_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_PLUGIN_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)


class APITransportError(RuntimeError):
    """A retryable transport failure. POST callers must not blindly resubmit."""


class APIResponseError(SystemExit):
    """Structured HTTP failure without retaining arbitrary response content."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class GitOperationError(SystemExit):
    """A Git failure with an explicit non-zero process exit code."""

    def __init__(self, message: str, returncode: int = 1):
        super().__init__(returncode if returncode > 0 else 1)
        self.message = message

    def __str__(self) -> str:
        return self.message


def redact_sensitive(value, key: str = ""):
    """Redact credential-shaped values and values stored under secret-bearing keys."""
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact_sensitive(item_value, str(item_key))
                for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item, key) for item in value[:500]]
    if isinstance(value, str):
        return _CHECKPOINT_SECRET.sub("[REDACTED]", value[:4000])
    return value


def redact_text(value: str, *extra_secrets: str) -> str:
    safe = str(value)
    for secret in sorted((str(item) for item in extra_secrets if item), key=len,
                         reverse=True):
        safe = safe.replace(secret, "[REDACTED]")
    return _CHECKPOINT_SECRET.sub("[REDACTED]", safe)


def format_api_error(status: int, detail: str) -> str:
    """Preserve the frozen error envelope without echoing arbitrary response bodies."""
    try:
        payload = json.loads(detail)
    except (TypeError, ValueError):
        safe = redact_text(str(detail))[:1000]
        return f"Agentour API {status}: {safe}"
    if not isinstance(payload, dict):
        return f"Agentour API {status}: invalid error response"
    allowed = ("error", "error_code", "request_id", "correlation_id", "stage",
               "target_service", "retryable", "details")
    safe_payload = redact_sensitive(
        {key: payload.get(key) for key in allowed if key in payload})
    safe_text = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))
    return f"Agentour API {status}: {safe_text}"


def base_url(platform: str) -> str:
    credentials=get_credentials(platform)
    if credentials.get("credential_type")=="tenant_access_token_v1":
        value=str(credentials.get("api_origin") or "").rstrip("/")
        parsed=urllib.parse.urlparse(value)
        loopback=parsed.hostname in {"127.0.0.1","localhost","::1"}
        if (parsed.scheme=="https" or (parsed.scheme=="http" and loopback)) and parsed.netloc and \
                not parsed.username and not parsed.password and parsed.path in {"","/"}:
            return value
        raise SystemExit("TENANT_API_ORIGIN_INVALID")
    return PLATFORMS[platform]["url"]


def request(platform: str, path: str, *, method: str = "GET",
            data: bytes | None = None, auth: bool = False,
            content_type: str = "application/json",
            extra_headers: dict[str, str] | None = None,
            required_scopes: tuple[str, ...] = (), interactive_auth: bool = False):
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = content_type
    headers.update(extra_headers or {})
    if auth:
        try:token=access_token(platform,base_url(platform),interactive=interactive_auth,
                               required_scopes=required_scopes)
        except OAuthClientError as exc:raise SystemExit(str(exc)) from exc
        headers["Authorization"] = f"Bearer {token}"
    attempts = 4 if method in {"GET", "HEAD"} else 1
    for attempt in range(attempts):
        req = urllib.request.Request(base_url(platform) + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code in {502, 503, 504} and attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
                continue
            if auth and exc.code==401:delete_credentials(platform)
            raise APIResponseError(exc.code, format_api_error(exc.code, detail)) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
                continue
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


def authenticated(args, path: str, *, method: str = "GET", body: dict | None = None,
                  idempotency_key: str = "", required_scopes: tuple[str, ...] = (),
                  interactive_auth: bool = False):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    return request(args.platform, path, method=method, data=data, auth=True,
                   extra_headers=headers,required_scopes=required_scopes,
                   interactive_auth=interactive_auth)


def optional_collection(args, path: str, *, capability: str) -> list:
    """Read a retired optional collection while failing closed for every other error."""
    try:
        result = authenticated(args, path)
    except APIResponseError as exc:
        if exc.status != 404:
            raise
        record_flight(
            "optional_capability_unavailable",
            platform=args.platform,
            capability=capability,
            endpoint=path.split("?", 1)[0],
            http_status=404,
            behavior="treated_as_empty_collection",
        )
        return []
    if not isinstance(result, list):
        raise SystemExit(f"{capability} response must be a list")
    return result


def stable_idempotency_key(operation: str, *parts: str) -> str:
    """Return the same bounded key for the same immutable creation request."""
    normalized = "\x1f".join(str(part).strip() for part in parts)
    digest = hashlib.sha256(
        f"{FORGE_CONTRACT_VERSION}\x1f{operation}\x1f{normalized}".encode("utf-8")
    ).hexdigest()
    label = re.sub(r"[^a-z0-9-]+", "-", operation.lower()).strip("-") or "create"
    return f"agentour-{label}-{digest[:40]}"


def one_time_idempotency_key(operation: str) -> str:
    """Create a non-replayable key for one-time plaintext credential exchange."""
    label = re.sub(r"[^a-z0-9-]+", "-", operation.lower()).strip("-") or "create"
    return f"agentour-{label}-{secrets.token_hex(20)}"


def bounded_int(value: str, *, minimum: int, maximum: int, option: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"{option} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"{option} must be between {minimum} and {maximum}")
    return parsed


def credential_ttl(value: str) -> int:
    return bounded_int(value, minimum=60, maximum=900, option="--credential-ttl")


def credential_max_uses(value: str) -> int:
    return bounded_int(value, minimum=1, maximum=100,
                       option="--credential-max-uses")


def repository_limit(value: str) -> int:
    return bounded_int(value, minimum=1, maximum=200, option="--limit")


def pull_request_number(value: str) -> int:
    return bounded_int(value, minimum=1, maximum=2_147_483_647,
                       option="--pull-request-number")


def commit_sha_argument(value: str, *, option: str) -> str:
    normalized = str(value or "").lower()
    if not _FULL_COMMIT_SHA.fullmatch(normalized):
        raise argparse.ArgumentTypeError(f"{option} must be a full Commit SHA")
    return normalized


def full_commit_sha(value: str) -> str:
    return commit_sha_argument(value, option="--source-commit-sha")


def expected_head_commit_sha(value: str) -> str:
    return commit_sha_argument(value, option="--expected-head-commit-sha")


def is_full_commit_sha(value: str) -> bool:
    return _FULL_COMMIT_SHA.fullmatch(str(value or "")) is not None


def cmd_repositories(args):
    query = urllib.parse.urlencode({"limit": args.limit, **({"cursor": args.cursor}
                                     if args.cursor else {})})
    result = authenticated(args, f"/v1/forge/repositories?{query}")
    record_flight("repositories_listed", result_count=len(result.get("items") or []),
                  contract_version=result.get("forge_contract_version", FORGE_CONTRACT_VERSION))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_repository_create(args):
    body = {"kind": args.kind, "canonical_name": args.name,
            "default_branch": args.default_branch, "visibility": args.visibility}
    key = stable_idempotency_key("repository-create", args.kind, args.name.lower(),
                                 args.default_branch, args.visibility)
    result = authenticated(args, "/v1/forge/repositories", method="POST", body=body,
                           idempotency_key=key)
    record_flight("repository_create_submitted", repository_id=result.get("repository_id"),
                  state=result.get("state"), contract_version=result.get(
                      "forge_contract_version", FORGE_CONTRACT_VERSION))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_repository_status(args):
    rid = urllib.parse.quote(args.repository_id, safe="")
    result = authenticated(args, f"/v1/forge/repositories/{rid}")
    record_flight("repository_status_read", repository_id=args.repository_id,
                  state=result.get("state"), contract_version=result.get(
                      "forge_contract_version", FORGE_CONTRACT_VERSION))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def issue_git_credential(args, repository_id: str, action: str) -> dict:
    """Issue one short-lived credential without printing or recording its plaintext."""
    if action not in {"read", "write"}:
        raise SystemExit("Agentour Git credential action must be read or write")
    try:
        ttl_seconds = int(args.credential_ttl)
        max_uses = int(args.credential_max_uses)
    except (TypeError, ValueError) as exc:
        raise SystemExit("Agentour Git credential limits must be integers") from exc
    if not 60 <= ttl_seconds <= 900:
        raise SystemExit("--credential-ttl must be between 60 and 900")
    if not 1 <= max_uses <= 100:
        raise SystemExit("--credential-max-uses must be between 1 and 100")
    body = {"repository_id": repository_id, "action": action,
            "ttl_seconds": ttl_seconds, "max_uses": max_uses}
    result = authenticated(args, "/v1/forge/git-credentials", method="POST", body=body,
                           idempotency_key=one_time_idempotency_key("git-credential"))
    required = ("credential_id", "username", "credential", "clone_url", "expires_at")
    if any(not str(result.get(key) or "") for key in required):
        raise SystemExit("Agentour Git credential response is incomplete")
    parsed = urllib.parse.urlsplit(str(result["clone_url"]))
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise SystemExit("Agentour Git clone URL must be credential-free HTTPS")
    return result


def run_git_with_credential(command: list[str], credential: dict, *, cwd: pathlib.Path | None,
                            timeout: float) -> subprocess.CompletedProcess:
    """Run Git with an environment-only secret and a credential-free askpass helper."""
    secret = str(credential["credential"])
    username = str(credential["username"])
    with tempfile.TemporaryDirectory(prefix="agentour-git-askpass-") as temp_dir:
        helper_dir = pathlib.Path(temp_dir)
        helper = helper_dir / "askpass.py"
        helper.write_text(
            "import os, sys\n"
            "prompt = (sys.argv[-1] if len(sys.argv) > 1 else '').lower()\n"
            "print(os.environ['AGENTOUR_GIT_USERNAME'] if 'username' in prompt "
            "else os.environ['AGENTOUR_GIT_CREDENTIAL'])\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = helper_dir / "askpass.cmd"
            launcher.write_text(
                f'@echo off\n@"{sys.executable}" "{helper}" "%~1"\n', encoding="utf-8")
        else:
            launcher = helper_dir / "askpass"
            launcher.write_text(f"#!{sys.executable}\n" + helper.read_text(encoding="utf-8"),
                                encoding="utf-8")
            os.chmod(launcher, 0o700)
        env = {**os.environ, "GIT_ASKPASS": str(launcher), "GIT_ASKPASS_REQUIRE": "force",
               "GIT_TERMINAL_PROMPT": "0", "AGENTOUR_GIT_USERNAME": username,
               "AGENTOUR_GIT_CREDENTIAL": secret}
        safe_command = [command[0], "-c", "credential.helper=", "-c",
                        f"core.hooksPath={helper_dir / 'disabled-hooks'}", *command[1:]]
        result = subprocess.run(safe_command, cwd=cwd, text=True, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=timeout, env=env)
    if result.returncode != 0:
        safe_output = redact_text(result.stdout + result.stderr, secret)[-4000:]
        raise GitOperationError(f"Git command failed: {safe_output}", result.returncode)
    return result


def cmd_git_clone(args):
    if not is_full_commit_sha(args.commit_sha):
        raise SystemExit("git-clone requires a full fixed Commit SHA")
    destination = pathlib.Path(args.destination).expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise SystemExit(f"Git clone destination is not a directory: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"Git clone destination is not empty: {destination}")
    credential = issue_git_credential(args, args.repository_id, "read")
    run_git_with_credential(
        ["git", "clone", "--no-checkout", str(credential["clone_url"]), str(destination)],
        credential, cwd=None, timeout=args.timeout)
    checkout = subprocess.run(["git", "-C", str(destination), "checkout", "--detach",
                               args.commit_sha.lower()], text=True, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=args.timeout)
    if checkout.returncode != 0:
        safe_output = redact_text(checkout.stdout + checkout.stderr,
                                  str(credential["credential"]))[-4000:]
        raise GitOperationError(f"Git checkout failed: {safe_output}", checkout.returncode)
    actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"],
                                     text=True, encoding="utf-8", errors="replace").strip()
    if actual.lower() != args.commit_sha.lower():
        raise SystemExit("Cloned Git baseline does not match the requested fixed Commit")
    record_flight("managed_repository_cloned", repository_id=args.repository_id,
                  commit_sha=actual.lower(), destination=str(destination))
    print(json.dumps({"repository_id": args.repository_id, "commit_sha": actual.lower(),
                      "destination": str(destination)}, ensure_ascii=False, indent=2))


def cmd_git_push(args):
    workspace = pathlib.Path(args.workspace).expanduser().resolve()
    if not (workspace / ".git").exists():
        raise SystemExit(f"Git workspace is invalid: {workspace}")
    if not is_full_commit_sha(args.commit_sha):
        raise SystemExit("git-push requires a full fixed Commit SHA")
    branch_check = subprocess.run(["git", "check-ref-format", "--branch", args.branch],
                                  text=True, capture_output=True, encoding="utf-8",
                                  errors="replace")
    if branch_check.returncode != 0:
        raise SystemExit("git-push branch name is invalid")
    commit_check = subprocess.run(["git", "-C", str(workspace), "cat-file", "-e",
                                   f"{args.commit_sha}^{{commit}}"], text=True,
                                  capture_output=True, encoding="utf-8", errors="replace")
    if commit_check.returncode != 0:
        raise SystemExit("git-push Commit does not exist in the local workspace")
    credential = issue_git_credential(args, args.repository_id, "write")
    run_git_with_credential(
        ["git", "push", str(credential["clone_url"]),
         f"{args.commit_sha.lower()}:refs/heads/{args.branch}"],
        credential, cwd=workspace, timeout=args.timeout)
    record_flight("managed_repository_pushed", repository_id=args.repository_id,
                  commit_sha=args.commit_sha.lower(), branch=args.branch)
    print(json.dumps({"repository_id": args.repository_id,
                      "commit_sha": args.commit_sha.lower(), "branch": args.branch,
                      "pushed": True}, ensure_ascii=False, indent=2))


def _git(workspace: pathlib.Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(workspace), *arguments], text=True,
                            capture_output=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise GitOperationError(
            "Git workspace operation failed: " +
            redact_text(result.stdout + result.stderr)[-4000:], result.returncode)
    return result.stdout.strip()


def _source_metadata_path(workspace: pathlib.Path) -> pathlib.Path:
    return workspace / ".agentour" / "source.json"


def _read_source_metadata(workspace: pathlib.Path) -> dict:
    path = _source_metadata_path(workspace)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("Agentour source metadata is invalid") from exc
    allowed = {"agent_id", "repository_id", "default_branch", "contract_version"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise SystemExit("Agentour source metadata contains unsupported fields")
    return value


def _write_source_metadata(workspace: pathlib.Path, *, agent_id: str,
                           repository_id: str, default_branch: str) -> None:
    path = _source_metadata_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agent_id": agent_id, "repository_id": repository_id,
                                "default_branch": default_branch,
                                "contract_version": FORGE_CONTRACT_VERSION},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _repository_for_source(args) -> dict:
    if args.repository_id:
        rid = urllib.parse.quote(args.repository_id, safe="")
        result = authenticated(args, f"/v1/forge/repositories/{rid}")
        if str(result.get("repository_id") or "") != args.repository_id:
            raise SystemExit("Core returned a different Repository identity")
        return result
    query = urllib.parse.urlencode({"limit": 200})
    listed = authenticated(args, f"/v1/forge/repositories?{query}")
    matches = [item for item in (listed.get("items") or [])
               if str(item.get("canonical_name") or item.get("name") or "") == args.name]
    if len(matches) > 1:
        raise SystemExit("Multiple owned Repositories match this Agent; specify --repository-id")
    if matches:
        return matches[0]
    body = {"kind": "agent", "canonical_name": args.name,
            "default_branch": args.default_branch, "visibility": "private"}
    return authenticated(args, "/v1/forge/repositories", method="POST", body=body,
                         idempotency_key=stable_idempotency_key(
                             "agent-source", args.agent_id, args.name, args.default_branch))


def _wait_repository_active(args, repository: dict) -> dict:
    repository_id = str(repository.get("repository_id") or "")
    deadline = time.monotonic() + max(1.0, float(args.timeout))
    current = repository
    while str(current.get("state") or "") not in {"active", "ready"}:
        if str(current.get("state") or "") in {"failed", "archived", "deleted"}:
            raise SystemExit("Agent source Repository did not become active")
        if time.monotonic() >= deadline:
            raise SystemExit("Timed out waiting for Agent source Repository projection")
        time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))
        rid = urllib.parse.quote(repository_id, safe="")
        current = authenticated(args, f"/v1/forge/repositories/{rid}")
    return current


def _ensure_agent_collection(args, agent_id: str, repository_id: str) -> dict:
    operation = stable_idempotency_key("agent-collection", agent_id, repository_id)
    result = authenticated(
        args,
        f"/v1/plugin/agents/{urllib.parse.quote(agent_id, safe='')}/collection:ensure",
        method="POST",
        body={"repository_id": repository_id, "operation_id": operation},
        idempotency_key=operation,
        required_scopes=("agent:write", "drive:file:write"),
        interactive_auth=True,
    )
    if (str(result.get("agent_id") or "") != agent_id or
            str(result.get("repository_id") or "") != repository_id or
            not str(result.get("collection_id") or "")):
        raise SystemExit("Agentour Collection binding response is invalid")
    return result


def cmd_agent_source_prepare(args):
    """Resolve/create the owned Repository and safely prepare an update workspace."""
    workspace = pathlib.Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    repository = _wait_repository_active(args, _repository_for_source(args))
    repository_id = str(repository.get("repository_id") or "")
    if not repository_id:
        raise SystemExit("Core Repository response has no immutable Repository ID")
    default_branch = str(repository.get("default_branch") or args.default_branch)
    _ensure_agent_collection(args, args.agent_id, repository_id)
    metadata = _read_source_metadata(workspace)
    if metadata and (str(metadata.get("agent_id")) != args.agent_id or
                     str(metadata.get("repository_id")) != repository_id):
        raise SystemExit("Local Agent/Repository binding conflicts with the resolved Repository")
    git_dir = workspace / ".git"
    if not git_dir.exists() and (workspace / "agentour.json").is_file():
        raise SystemExit(
            "Source workspace must be a Git repository root; pass the parent workspace and --path Package"
        )
    if not git_dir.exists():
        if any(item.name != ".agentour" for item in workspace.iterdir()):
            _git(workspace, "init", "-b", default_branch)
        else:
            credential = issue_git_credential(args, repository_id, "read")
            run_git_with_credential(
                ["git", "clone", "--branch", default_branch, "--single-branch",
                 str(credential["clone_url"]), str(workspace)],
                credential, cwd=None, timeout=args.timeout)
            if args.ref:
                run_git_with_credential(
                    ["git", "fetch", "--no-tags", str(credential["clone_url"]), args.ref],
                    credential, cwd=workspace, timeout=args.timeout)
                _git(workspace, "checkout", "--detach", "FETCH_HEAD")
    else:
        dirty = bool(_git(workspace, "status", "--porcelain"))
        if dirty and not args.use_local:
            raise SystemExit("Workspace has local changes; use --use-local or commit them before updating")
        credential = issue_git_credential(args, repository_id, "read")
        refspec = args.ref or default_branch
        run_git_with_credential(["git", "fetch", "--no-tags", str(credential["clone_url"]),
                                 refspec], credential, cwd=workspace, timeout=args.timeout)
        remote_commit = _git(workspace, "rev-parse", "FETCH_HEAD")
        local_commit = _git(workspace, "rev-parse", "HEAD", check=False)
        if local_commit and not dirty:
            base = _git(workspace, "merge-base", local_commit, remote_commit, check=False)
            if base == local_commit and local_commit != remote_commit:
                _git(workspace, "merge", "--ff-only", remote_commit)
            elif base not in {local_commit, remote_commit}:
                raise SystemExit("Local and remote source diverged; choose merge, rebase, or a new branch")
        elif not local_commit:
            _git(workspace, "checkout", "--detach", remote_commit)
    _write_source_metadata(workspace, agent_id=args.agent_id,
                           repository_id=repository_id, default_branch=default_branch)
    commit = _git(workspace, "rev-parse", "HEAD", check=False)
    record_flight("agent_source_prepared", repository_id=repository_id,
                  commit_sha=commit, agent_id=args.agent_id)
    print(json.dumps({"agent_id": args.agent_id, "repository_id": repository_id,
                      "workspace": str(workspace), "commit_sha": commit or None,
                      "default_branch": default_branch}, ensure_ascii=False, indent=2))


def cmd_agent_source_push(args):
    """Map one validated Package to Repository root and push one exact Commit."""
    workspace = pathlib.Path(args.workspace).expanduser().resolve()
    if not (workspace / ".git").exists():
        raise SystemExit("Agent source workspace is not a Git repository")
    metadata = _read_source_metadata(workspace)
    if (str(metadata.get("agent_id") or "") != args.agent_id or
            str(metadata.get("repository_id") or "") != args.repository_id):
        raise SystemExit("Local Agent/Repository binding does not match the push request")
    if _git(workspace, "diff", "--cached", "--name-only"):
        raise SystemExit("Workspace already has staged changes; commit or unstage them first")
    if len(args.path) != 1:
        raise SystemExit("Agent source push requires exactly one Package directory")
    selected: list[str] = []
    for raw_path in args.path:
        candidate = (workspace / raw_path).resolve()
        try:
            relative = candidate.relative_to(workspace).as_posix()
        except ValueError as exc:
            raise SystemExit("Agent source path escapes the workspace") from exc
        if relative in {"", ".", ".git"} or relative.startswith(".git/"):
            raise SystemExit("Agent source path cannot include Git control data")
        selected.append(relative)
    package_root = workspace / selected[0]
    if not package_root.is_dir() or not (package_root / "agentour.json").is_file():
        raise SystemExit("Agent source path must be a Package directory containing agentour.json")
    package_files: dict[str, bytes] = {}
    for item in sorted(package_root.rglob("*")):
        if item.is_symlink():
            raise SystemExit("Agent Package cannot contain symbolic links")
        if not item.is_file():
            continue
        relative = item.relative_to(package_root).as_posix()
        if relative == ".agentour" or relative.startswith((".agentour/", ".git/")):
            raise SystemExit("Agent Package cannot contain Compiler or Git control data")
        package_files[relative] = item.read_bytes()
    if not package_files:
        raise SystemExit("Agent Package is empty")
    tracked = [item for item in _git(workspace, "ls-files").splitlines() if item]
    if any(item == ".agentour" or item.startswith(".agentour/") for item in tracked):
        raise SystemExit("Repository contains tracked Compiler state; remove it before pushing")
    dirty = [item for item in _git(workspace, "diff", "--name-only").splitlines() if item]
    package_prefix = selected[0].rstrip("/") + "/"
    unexpected_dirty = [item for item in dirty if not item.startswith(package_prefix)]
    if unexpected_dirty:
        raise SystemExit("Workspace has tracked changes outside the selected Package")

    # A managed Agent Repository is an immutable projection of exactly one Package. Clear the
    # index (not the working files), then stage only the snapshotted Package bytes at Repository
    # root. Compiler state and unrelated untracked workspace files can therefore never leak in.
    if tracked:
        _git(workspace, "rm", "-r", "--cached", "--ignore-unmatch", "--", ".")
    for relative, content in package_files.items():
        destination = workspace / pathlib.PurePosixPath(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    root_paths = sorted(package_files)
    _git(workspace, "add", "--", *root_paths)
    tree = _git(workspace, "write-tree")
    tree_paths = [item for item in _git(workspace, "ls-tree", "-r", "--name-only", tree).splitlines()
                  if item]
    if tree_paths != root_paths:
        raise SystemExit("Staged Agent Repository tree does not exactly match the Package root")
    staged = _git(workspace, "diff", "--cached", "--name-only")
    if staged:
        _git(workspace, "commit", "-m", args.message)
    commit_sha = _git(workspace, "rev-parse", "HEAD")
    if not is_full_commit_sha(commit_sha):
        raise SystemExit("Git did not produce a full immutable Commit SHA")
    branch = args.branch or str(metadata.get("default_branch") or "main")
    branch_check = subprocess.run(["git", "check-ref-format", "--branch", branch],
                                  text=True, capture_output=True, encoding="utf-8",
                                  errors="replace")
    if branch_check.returncode != 0:
        raise SystemExit("Agent source branch name is invalid")
    credential = issue_git_credential(args, args.repository_id, "write")
    run_git_with_credential(
        ["git", "push", str(credential["clone_url"]),
         f"{commit_sha}:refs/heads/{branch}"],
        credential, cwd=workspace, timeout=args.timeout)
    record_flight("agent_source_pushed", agent_id=args.agent_id,
                  repository_id=args.repository_id, commit_sha=commit_sha, branch=branch,
                  package_path=selected[0], package_files=len(root_paths),
                  removed_tracked_paths=sorted(set(tracked) - set(root_paths)))
    print(json.dumps({"agent_id": args.agent_id, "repository_id": args.repository_id,
                      "commit_sha": commit_sha, "branch": branch, "pushed": True},
                     ensure_ascii=False, indent=2))


def cmd_repository(args):
    """Read a tenant-scoped repository summary through Core's Forge contract."""
    rid = urllib.parse.quote(args.repository_id, safe="")
    result = authenticated(args, f"/v1/dev/repositories/{rid}")
    record_flight("repository_read", repository_id=args.repository_id,
                  contract_version=result.get("contract_version", FORGE_CONTRACT_VERSION))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def validate_pull_request_response(result, *, repository_id: str,
                                   pull_request_number: int | None = None,
                                   expected_head_commit_sha: str = "",
                                   require_merged: bool = False) -> tuple[int, str, str]:
    """Fail closed when Core's frozen PR response is not Commit-bound."""
    if not isinstance(result, dict):
        raise SystemExit("Core Pull Request response is invalid")
    try:
        response_number = int(result.get("pull_request_number"))
    except (TypeError, ValueError) as exc:
        raise SystemExit("Core Pull Request response is invalid") from exc
    head = result.get("head") if isinstance(result.get("head"), dict) else {}
    head_commit = str(head.get("commit_sha") or "").lower()
    merge_commit = str(result.get("merge_commit_sha") or "").lower()
    if (str(result.get("repository_id") or "") != repository_id or
            response_number < 1 or
            (pull_request_number is not None and response_number != pull_request_number) or
            not is_full_commit_sha(head_commit) or
            (expected_head_commit_sha and head_commit != expected_head_commit_sha.lower()) or
            str(result.get("contract_version") or "") != FORGE_CONTRACT_VERSION):
        raise SystemExit("Core Pull Request response is not Commit-bound")
    if require_merged and (result.get("merged") is not True or
                           str(result.get("status") or "") != "merged" or
                           not is_full_commit_sha(merge_commit)):
        raise SystemExit("Core Pull Request merge response is not immutable")
    return response_number, head_commit, merge_commit


def cmd_change_set_create(args):
    """Create or recover one Core-managed Pull Request for an exact pushed head."""
    if not is_full_commit_sha(args.expected_head_commit_sha):
        raise SystemExit("change-set-create requires a full expected head Commit SHA")
    rid = urllib.parse.quote(args.repository_id, safe="")
    body = {
        "head_ref": args.head_ref,
        "expected_head_commit_sha": args.expected_head_commit_sha.lower(),
        "title": args.title,
        "body": args.body,
    }
    key = stable_idempotency_key(
        "change-set-create", args.repository_id, args.head_ref,
        args.expected_head_commit_sha.lower(), args.title, args.body,
    )
    result = authenticated(
        args, f"/v1/dev/repositories/{rid}/change-sets",
        method="POST", body=body, idempotency_key=key,
    )
    response_number, head_commit, _ = validate_pull_request_response(
        result, repository_id=args.repository_id,
        expected_head_commit_sha=args.expected_head_commit_sha,
    )
    record_flight(
        "change_set_created", repository_id=args.repository_id,
        pull_request_number=response_number, commit_sha=head_commit,
        status=result.get("status"), contract_version=result["contract_version"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_pull_request_status(args):
    """Read current Provider PR and Review facts without changing them."""
    rid = urllib.parse.quote(args.repository_id, safe="")
    result = authenticated(
        args,
        f"/v1/dev/repositories/{rid}/pull-requests/{args.pull_request_number}",
    )
    _, head_commit, _ = validate_pull_request_response(
        result, repository_id=args.repository_id,
        pull_request_number=args.pull_request_number,
    )
    reviews_total = result.get("reviews_total")
    if (isinstance(reviews_total, bool) or not isinstance(reviews_total, int) or
            reviews_total < 0):
        raise SystemExit("Core Pull Request Review response is invalid")
    record_flight(
        "pull_request_read", repository_id=args.repository_id,
        pull_request_number=args.pull_request_number,
        commit_sha=head_commit, status=result.get("status"),
        reviews_total=reviews_total, contract_version=result["contract_version"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_pull_request_merge(args):
    """Ask Core to merge an exact PR head after authoritative policy checks."""
    if not is_full_commit_sha(args.expected_head_commit_sha):
        raise SystemExit("pull-request-merge requires a full expected head Commit SHA")
    rid = urllib.parse.quote(args.repository_id, safe="")
    expected_head = args.expected_head_commit_sha.lower()
    body = {
        "expected_head_commit_sha": expected_head,
        "required_approvals": args.required_approvals,
    }
    key = stable_idempotency_key(
        "pull-request-merge", args.repository_id,
        str(args.pull_request_number), expected_head, str(args.required_approvals),
    )
    result = authenticated(
        args,
        f"/v1/dev/repositories/{rid}/pull-requests/{args.pull_request_number}/merge",
        method="POST", body=body, idempotency_key=key,
        required_scopes=("repository:release",), interactive_auth=True,
    )
    _, _, merge_commit = validate_pull_request_response(
        result, repository_id=args.repository_id,
        pull_request_number=args.pull_request_number,
        expected_head_commit_sha=expected_head, require_merged=True,
    )
    record_flight(
        "pull_request_merged", repository_id=args.repository_id,
        pull_request_number=args.pull_request_number,
        commit_sha=merge_commit,
        required_approvals=args.required_approvals,
        status=result.get("status"), contract_version=result["contract_version"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_source_revision(args):
    """Create a fixed source revision request; the server remains authoritative."""
    if not is_full_commit_sha(args.commit_sha):
        raise SystemExit("source-revision requires a full fixed Commit SHA")
    rid = urllib.parse.quote(args.repository_id, safe="")
    body = {
        "commit_sha": args.commit_sha,
        "pull_request_number": args.pull_request_number,
    }
    key = stable_idempotency_key("source-revision", args.repository_id,
                                 args.commit_sha.lower(), str(args.pull_request_number))
    result = authenticated(args, f"/v1/dev/repositories/{rid}/source-revisions",
                           method="POST", body=body, idempotency_key=key)
    record_flight("source_revision_submitted", repository_id=args.repository_id,
                  commit_sha=args.commit_sha, pull_request_number=args.pull_request_number,
                  source_revision_id=result.get("source_revision_id"),
                  contract_version=FORGE_CONTRACT_VERSION)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_source_revision_status(args):
    source_revision_id = urllib.parse.quote(args.source_revision_id, safe="")
    result = authenticated(args, f"/v1/dev/source-revisions/{source_revision_id}")
    record_flight("source_revision_read", source_revision_id=args.source_revision_id,
                  status=result.get("status"), contract_version=FORGE_CONTRACT_VERSION)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_source_build(args):
    source_revision_id = urllib.parse.quote(args.source_revision_id, safe="")
    key = stable_idempotency_key("source-build", args.source_revision_id)
    result = authenticated(args, f"/v1/dev/source-revisions/{source_revision_id}/builds",
                           method="POST", body={}, idempotency_key=key)
    record_flight("source_build_submitted", source_revision_id=args.source_revision_id,
                  remote_job_id=result.get("build_id"), status=result.get("status"),
                  contract_version=FORGE_CONTRACT_VERSION)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_source_build_status(args):
    """Read an existing Source Revision Build without resubmitting work."""
    build_id = urllib.parse.quote(args.build_id, safe="")
    result = authenticated(args, f"/v1/dev/source-builds/{build_id}")
    record_flight("source_build_read", remote_job_id=args.build_id,
                  source_revision_id=result.get("source_revision_id"),
                  status=result.get("status"), error_code=result.get("error_code"),
                  contract_version=result.get("contract_version", FORGE_CONTRACT_VERSION))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_source_eval(args):
    source_revision_id = urllib.parse.quote(args.source_revision_id, safe="")
    key = stable_idempotency_key("source-eval", args.source_revision_id)
    result = authenticated(args, f"/v1/dev/source-revisions/{source_revision_id}/eval-runs",
                           method="POST", body={}, idempotency_key=key)
    record_flight("source_eval_submitted", source_revision_id=args.source_revision_id,
                  remote_job_id=result.get("eval_run_id"), status=result.get("status"),
                  contract_version=FORGE_CONTRACT_VERSION)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_source_eval_status(args):
    """Read an existing Source Revision Eval without resubmitting work."""
    eval_run_id = urllib.parse.quote(args.eval_run_id, safe="")
    result = authenticated(args, f"/v1/dev/source-eval-runs/{eval_run_id}")
    record_flight("source_eval_read", remote_job_id=args.eval_run_id,
                  source_revision_id=result.get("source_revision_id"),
                  build_id=result.get("build_id"), status=result.get("status"),
                  error_code=result.get("error_code"),
                  contract_version=result.get("contract_version", FORGE_CONTRACT_VERSION))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_agent_release(args):
    """Run the one authoritative Core/Forge/Drive Agent release transaction."""
    # A merged PR's immutable source is the repository main commit; using the
    # feature ref here causes Core to report source_commit drift.
    source_ref = "main" if args.pull_request_number else args.source_ref
    operation = stable_idempotency_key(
        "agent-release", args.agent_id, args.version, args.source_revision_id,
        args.source_commit_sha, source_ref,
    )
    body = {
        "operation_id": operation,
        "version": args.version,
        "source_ref": source_ref,
        "source_revision_id": args.source_revision_id,
        "source_commit_sha": args.source_commit_sha,
        "pull_request_number": args.pull_request_number,
        "required_approvals": args.required_approvals,
        "release_notes": args.release_notes,
        "visibility": getattr(args,"visibility","private"),
    }
    result = authenticated(
        args,
        f"/v1/plugin/agents/{urllib.parse.quote(args.agent_id, safe='')}/releases",
        method="POST",
        body=body,
        idempotency_key=operation,
        required_scopes=("agent:publish",),
        interactive_auth=True,
    )
    state = str(result.get("state") or "")
    if (str(result.get("agent_id") or "") != args.agent_id or
            str(result.get("version") or "") != args.version or
            str(result.get("source_commit_sha") or "").lower() !=
            args.source_commit_sha.lower() or
            str(result.get("operation_id") or "") != operation or
            not str(result.get("repository_id") or "") or
            not str(result.get("collection_id") or "") or
            state not in {"completed", "failed"}):
        raise SystemExit("Agentour unified release response is invalid")
    completed_evidence = (
        "main_commit_sha", "drive_snapshot_id", "drive_snapshot_digest",
        "forge_tag_id", "forge_release_id", "core_version_id", "completed_at",
    )
    if state == "completed" and any(not str(result.get(key) or "")
                                      for key in completed_evidence):
        raise SystemExit("Agentour completed release evidence is incomplete")
    if state == "failed" and (not str(result.get("failed_stage") or "") or
                              not str(result.get("error_code") or "")):
        raise SystemExit("Agentour failed release evidence is incomplete")
    record_flight(
        "agent_release_completed",
        package_id=args.agent_id,
        source_revision_id=args.source_revision_id,
        release_id=result.get("id"),
        status=result.get("state"),
        contract_version=FORGE_CONTRACT_VERSION,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def validate_forge_checkpoint(value: dict) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Forge checkpoint must be a JSON object")
    unexpected = set(value) - FORGE_CHECKPOINT_FIELDS
    missing = FORGE_CHECKPOINT_FIELDS - set(value)
    if unexpected or missing:
        raise ValueError(
            "Forge checkpoint fields must be exactly: " +
            ", ".join(sorted(FORGE_CHECKPOINT_FIELDS))
        )
    checkpoint = {key: value[key] for key in FORGE_CHECKPOINT_FIELDS}
    if any(not isinstance(item, str) for item in checkpoint.values()):
        raise ValueError("Forge checkpoint values must be strings")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", checkpoint["repository_id"]):
        raise ValueError("Forge checkpoint repository_id is invalid")
    if not is_full_commit_sha(checkpoint["commit_sha"]):
        raise ValueError("Forge checkpoint commit_sha must be a full fixed SHA")
    if checkpoint["remote_job_id"] and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", checkpoint["remote_job_id"]):
        raise ValueError("Forge checkpoint remote_job_id is invalid")
    if checkpoint["contract_version"] != FORGE_CONTRACT_VERSION:
        raise ValueError("Forge checkpoint contract_version is incompatible")
    if checkpoint["stage"] not in FORGE_CHECKPOINT_STAGES:
        raise ValueError("Forge checkpoint stage is invalid")
    if any(_CHECKPOINT_SECRET.search(item) for item in checkpoint.values()):
        raise ValueError("Forge checkpoint must not contain credentials or secret material")
    return checkpoint


def write_forge_checkpoint(path: pathlib.Path, checkpoint: dict) -> dict[str, str]:
    clean = validate_forge_checkpoint(checkpoint)
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     delete=False) as handle:
        json.dump(clean, handle, ensure_ascii=False, indent=2, sort_keys=True)
        temp = pathlib.Path(handle.name)
    os.chmod(temp, 0o600)
    temp.replace(path)
    return clean


def read_forge_checkpoint(path: pathlib.Path, current_commit_sha: str = "") -> dict[str, str]:
    checkpoint = validate_forge_checkpoint(json.loads(
        path.expanduser().resolve().read_text(encoding="utf-8")
    ))
    if current_commit_sha:
        if not is_full_commit_sha(current_commit_sha):
            raise ValueError("current commit_sha must be a full fixed SHA")
        if checkpoint["commit_sha"].lower() != current_commit_sha.lower():
            checkpoint = {**checkpoint, "commit_sha": current_commit_sha.lower(),
                          "remote_job_id": "", "stage": "commit_changed"}
    return checkpoint


def cmd_save_forge_checkpoint(args):
    checkpoint = write_forge_checkpoint(pathlib.Path(args.path), {
        "repository_id": args.repository_id,
        "commit_sha": args.commit_sha.lower(),
        "remote_job_id": args.remote_job_id,
        "contract_version": args.contract_version,
        "stage": args.stage,
    })
    record_flight("forge_checkpoint_saved", repository_id=checkpoint["repository_id"],
                  commit_sha=checkpoint["commit_sha"],
                  remote_job_id=checkpoint["remote_job_id"], stage=checkpoint["stage"],
                  contract_version=checkpoint["contract_version"])
    print(json.dumps(checkpoint, ensure_ascii=False, indent=2))


def cmd_restore_forge_checkpoint(args):
    checkpoint = read_forge_checkpoint(pathlib.Path(args.path), args.current_commit_sha)
    record_flight("forge_checkpoint_restored", repository_id=checkpoint["repository_id"],
                  commit_sha=checkpoint["commit_sha"],
                  remote_job_id=checkpoint["remote_job_id"], stage=checkpoint["stage"],
                  contract_version=checkpoint["contract_version"])
    print(json.dumps(checkpoint, ensure_ascii=False, indent=2))


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


def patch_compiler_task(args, quoted_task_id: str, body: dict) -> dict:
    path=f"/v1/dev/compiler-tasks/{quoted_task_id}"
    patch=dict(body)
    if patch.get("expected_revision") is None:
        patch["expected_revision"]=authenticated(args,path).get("revision")
    for attempt in range(4):
        try:
            return authenticated(args,path,method="PATCH",body=patch)
        except APIResponseError as exc:
            if (exc.status!=409 or "compiler task revision conflict" not in str(exc)
                    or attempt==3):
                raise
            patch["expected_revision"]=authenticated(args,path).get("revision")
            time.sleep(0.05*(attempt+1))
    raise AssertionError("bounded Compiler Task update loop did not return")


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
        flight = read_flight()
        patch_compiler_task(args,quoted,{
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


def cmd_authorize(args):
    try:access_token(args.platform,base_url(args.platform),interactive=True)
    except OAuthClientError as exc:raise SystemExit(str(exc)) from exc
    result = authenticated(args, "/v1/dev/me")
    print(json.dumps({"valid": True, "platform": PLATFORMS[args.platform]["name"],
                      "developer_id": result.get("developer_id")}, ensure_ascii=False), flush=True)


def cmd_account(args):
    credentials=get_credentials(args.platform)
    print(json.dumps({"platform":PLATFORMS[args.platform]["name"],
        "authorized":credentials.get("credential_type")=="oauth_public_client_v1",
        "display_name":str(credentials.get("display_name") or ""),
        "subject":str(credentials.get("subject") or "")},ensure_ascii=False),flush=True)


def cmd_switch_account(args):
    try:identity=switch_account(args.platform,base_url(args.platform))
    except OAuthClientError as exc:raise SystemExit(str(exc)) from exc
    print(json.dumps({"switched":True,"platform":PLATFORMS[args.platform]["name"],
        "display_name":identity.get("display_name"),"subject":identity.get("subject")},
        ensure_ascii=False),flush=True)


def cmd_models(args):
    print(json.dumps(discover_models(args), ensure_ascii=False, indent=2), flush=True)


def cmd_check_update(args):
    result = check_update(auto=args.auto)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if result.get("blocked") or (
            result.get("outdated") and args.auto and not result.get("updated")):
        raise SystemExit(1)


def _prerelease_key(value: str):
    if not value:
        return (1,)
    result = [0]
    for item in value.split("."):
        result.append((0, int(item)) if item.isdigit() else (1, item))
    return tuple(result)


def plugin_version_identity(value: str) -> tuple[tuple, str]:
    """Return SemVer precedence plus the full cache identity."""
    match = _PLUGIN_VERSION.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError(f"invalid Plugin version: {value!r}")
    major, minor, patch, prerelease, _build = match.groups()
    precedence = (int(major), int(minor), int(patch),
                  _prerelease_key(prerelease or ""))
    return precedence, match.group(0)


def plugin_update_decision(current: str, latest: str) -> tuple[bool, str]:
    """Treat a changed Codex build identity as installable at equal SemVer."""
    current_precedence, current_identity = plugin_version_identity(current)
    latest_precedence, latest_identity = plugin_version_identity(latest)
    if latest_precedence > current_precedence:
        return True, "newer_semver"
    if latest_precedence < current_precedence:
        return False, "current_semver_newer"
    if latest_identity != current_identity:
        current_cache = re.search(r"\+codex\.([0-9]{14})$", current_identity)
        latest_cache = re.search(r"\+codex\.([0-9]{14})$", latest_identity)
        if current_cache and latest_cache and latest_cache.group(1) < current_cache.group(1):
            return False, "current_cache_identity_newer"
        return True, "cache_identity_changed"
    return False, "same_identity"


def codex_cli_command() -> list[str]:
    """Resolve the Codex launcher to an executable subprocess can start directly."""
    names = ("codex.cmd", "codex.exe", "codex") if os.name == "nt" else ("codex",)
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return [resolved]
    raise FileNotFoundError(
        "Codex CLI executable was not found on PATH; install Codex and restart the shell")


def check_update(*, auto: bool) -> dict:
    try:
        with urllib.request.urlopen(LATEST_MANIFEST_URL, timeout=15) as response:
            latest = str(json.loads(response.read()).get("version", ""))
    except Exception as exc:
        return {"checked": False, "current": PLUGIN_VERSION,
                "warning": f"无法检查 Plugin 更新: {exc}"}
    try:
        outdated, reason = plugin_update_decision(PLUGIN_VERSION, latest)
    except ValueError as exc:
        return {"checked": True, "current": PLUGIN_VERSION, "latest": latest,
                "outdated": False, "updated": False, "blocked": True,
                "error": str(exc)}
    result = {"checked": True, "current": PLUGIN_VERSION, "latest": latest,
              "outdated": outdated, "updated": False,
              "comparison_reason": reason}
    if outdated and auto:
        try:
            codex = codex_cli_command()
        except FileNotFoundError as exc:
            result["error"] = str(exc)
            return result
        refresh = subprocess.run(
            [*codex, "plugin", "marketplace", "upgrade", "agentour-platform"],
            text=True, capture_output=True, encoding="utf-8", errors="replace")
        completed = (subprocess.run(
            [*codex, "plugin", "add", "agentour-compiler@agentour-platform"],
            text=True, capture_output=True, encoding="utf-8", errors="replace")
                     if refresh.returncode == 0 else refresh)
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
        except (SystemExit, APITransportError) as exc:
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
    if update.get("blocked") or (update.get("outdated") and
                                 not update.get("updated")):
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
    try:
        access_token(platform,base_url(platform),interactive=True)
        identity = authenticated(args, "/v1/dev/me")
        contract = authenticated(args, "/v1/dev/compiler-contract")
        models = discover_models(args)
        tasks = authenticated(args, "/v1/dev/compiler-tasks?active=true")
        fix_tasks = optional_collection(
            args, "/v1/dev/fix-tasks?limit=200", capability="accepted_fix_tasks")
    except (SystemExit,OAuthClientError) as exc:
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
        "accepted_fix_tasks": [item for item in fix_tasks
                               if item.get("status") in {"accepted", "claimed", "fixing"}],
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


def cmd_build_test(args):
    package = pathlib.Path(args.package).resolve()
    payload = package / "payload"
    if not (payload / "package.json").is_file():
        raise SystemExit(f"Missing payload/package.json in {package}")
    host_build = False
    pnpm_command = "pnpm.cmd" if os.name == "nt" else "pnpm"
    try:
        node_version = subprocess.check_output(
            ["node", "--version"], text=True, encoding="utf-8", errors="replace").strip()
        host_build = int(node_version.lstrip("v").split(".", 1)[0]) >= 24
        if host_build:
            probes = ([pnpm_command, "--version"], ["corepack", "pnpm", "--version"])
            for probe in probes:
                try:
                    subprocess.run(probe, check=True, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                    pnpm_command = probe[:-1]
                    break
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            else:
                host_build = False
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        host_build = False
    if not host_build:
        docker = subprocess.run(["docker", "image", "inspect", "agentour-runtime:1"],
                                text=True, capture_output=True,
                                encoding="utf-8", errors="replace")
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
        pnpm_prefix = pnpm_command if isinstance(pnpm_command, list) else [pnpm_command]
        commands = ([
            [*pnpm_prefix, "install", "--frozen-lockfile"],
            [*pnpm_prefix, "exec", "eve", "build"],
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
                                    encoding="utf-8", errors="replace",
                                    timeout=args.timeout, env=build_env)
            if result.returncode != 0:
                record_flight("local_build_failed", package=str(package), command=command,
                              duration_seconds=round(time.time() - started_at, 3),
                              error=(result.stdout + result.stderr)[-4000:])
                raise SystemExit(f"{' '.join(command)} failed:\n{(result.stdout + result.stderr)[-4000:]}")
    finally:
        import shutil
        gc.collect()
        def make_writable(_function, path, _exc_info):
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        for attempt in range(10):
            try:
                shutil.rmtree(td, onerror=make_writable)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                if attempt == 9:
                    record_flight("temporary_directory_cleanup_failed", path=td, error=str(exc))
                else:
                    gc.collect()
                    time.sleep(min(2.0, 0.15 * (2 ** attempt)))
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
    state = load_json_argument(args.state, getattr(args, "state_file", None))
    body = {"operation": args.operation, "agent_id": args.agent_id,
            "platform": args.platform, "workspace_id": args.workspace_id,
            "state": state}
    result = authenticated(args, "/v1/dev/compiler-tasks", method="POST", body=body)
    record_flight("compiler_task_created", task=result)
    sync_flight(args, str(result.get("id", "")))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def cmd_update_compiler_task(args):
    body = {"state": load_json_argument(args.state, args.state_file)}
    for key in ("stage", "status", "package_hash", "expected_revision"):
        value = getattr(args, key, None)
        if value is not None:
            body[key] = value
    task_id = urllib.parse.quote(args.task_id, safe="")
    result=patch_compiler_task(args,task_id,body)
    record_flight("compiler_task_updated", task=result, patch=body)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def load_json_argument(value: str, filename: str) -> dict:
    """Prefer a UTF-8 JSON file on Windows, where nested shell quoting is fragile."""
    if filename:
        parsed = json.loads(pathlib.Path(filename).read_text(encoding="utf-8"))
    else:
        parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise SystemExit("Compiler Task state must be a JSON object")
    return parsed


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
    try:token=access_token(args.platform,base_url(args.platform))
    except OAuthClientError as exc:raise SystemExit(str(exc)) from exc
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


def cmd_upload_references(args):
    binding = _ensure_agent_collection(args, args.agent_id, args.repository_id)
    try:
        token = access_token(args.platform, base_url(args.platform))
    except OAuthClientError as exc:
        raise SystemExit(str(exc)) from exc
    uploaded = []
    for raw in args.files:
        path = pathlib.Path(raw).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"Reference file does not exist: {path}")
        content = path.read_bytes()
        if not 1 <= len(content) <= 100 * 1024 * 1024:
            raise SystemExit(f"Reference file size must be between 1 byte and 100 MiB: {path}")
        mime = __import__("mimetypes").guess_type(path.name)[0] or "application/octet-stream"
        digest = hashlib.sha256(content).hexdigest()
        operation = stable_idempotency_key(
            "agent-reference", args.agent_id, args.repository_id, path.name, digest)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": mime,
                   "Content-Length": str(len(content)),
                   "X-Filename": urllib.parse.quote(path.name, safe=""),
                   "Idempotency-Key": operation, "Accept": "application/json"}
        req = urllib.request.Request(
            base_url(args.platform) +
            f"/v1/plugin/agents/{urllib.parse.quote(args.agent_id, safe='')}/files:upload",
            data=content, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise SystemExit(format_api_error(exc.code, detail)) from exc
        if (str(result.get("agent_id") or "") != args.agent_id or
                str(result.get("collection_id") or "") != binding["collection_id"] or
                str(result.get("content_digest") or "") != "sha256:" + digest or
                not str(result.get("file_id") or "") or
                not str(result.get("file_version_id") or "")):
            raise SystemExit("Agentour reference upload response is invalid")
        uploaded.append(result)
    print(json.dumps({"agent_id": args.agent_id,
                      "repository_id": args.repository_id,
                      "collection_id": binding["collection_id"],
                      "uploaded": uploaded}, ensure_ascii=False, indent=2))


def cmd_complete_fix(args):
    payload_path = pathlib.Path(args.result).expanduser().resolve()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if not str(payload.get("analysis") or "").strip() or not str(payload.get("summary") or "").strip():
        raise SystemExit("Fix result must include non-empty analysis and summary")
    payload.setdefault("changes", [])
    payload.setdefault("evidence", {})
    payload.setdefault("commits", [])
    payload["plugin"] = "codex"
    payload["plugin_version"] = PLUGIN_VERSION
    task_id = urllib.parse.quote(args.task_id, safe="")
    result = authenticated(args, f"/v1/dev/fix-tasks/{task_id}/complete",
                           method="POST", body={"status": "verification", "payload": payload})
    record_flight("fix_task_completed", task_id=args.task_id,
                  improvement_id=(result.get("improvement") or {}).get("id"),
                  summary=payload["summary"], commits=payload["commits"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=PLATFORMS, default="production")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("platforms")
    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--target-platform", choices=PLATFORMS)
    sub.add_parser("authorize")
    sub.add_parser("account")
    sub.add_parser("switch-account")
    sub.add_parser("models")
    repositories = sub.add_parser("repositories")
    repositories.add_argument("--cursor", default="")
    repositories.add_argument("--limit", type=repository_limit, default=100)
    repository_create = sub.add_parser("repository-create")
    repository_create.add_argument("--kind", choices=("agent", "knowledge", "eval"),
                                   default="agent")
    repository_create.add_argument("--name", required=True)
    repository_create.add_argument("--default-branch", default="main")
    repository_create.add_argument("--visibility", choices=("private", "internal"),
                                   default="private")
    repository_status = sub.add_parser("repository-status")
    repository_status.add_argument("repository_id")
    repository = sub.add_parser("repository")
    repository.add_argument("repository_id")
    change_set_create = sub.add_parser(
        "change-set-create", help="create or recover a PR for one exact pushed head")
    change_set_create.add_argument("repository_id")
    change_set_create.add_argument("--head-ref", required=True)
    change_set_create.add_argument("--expected-head-commit-sha", required=True,
                                   type=expected_head_commit_sha)
    change_set_create.add_argument("--title", required=True)
    change_set_create.add_argument("--body", default="")
    pull_request_status = sub.add_parser(
        "pull-request-status", help="read current PR and Review facts")
    pull_request_status.add_argument("repository_id")
    pull_request_status.add_argument("pull_request_number", type=pull_request_number)
    pull_request_merge = sub.add_parser(
        "pull-request-merge", help="merge one exact PR head through Core policy")
    pull_request_merge.add_argument("repository_id")
    pull_request_merge.add_argument("pull_request_number", type=pull_request_number)
    pull_request_merge.add_argument("--expected-head-commit-sha", required=True,
                                    type=expected_head_commit_sha)
    pull_request_merge.add_argument("--required-approvals", type=lambda value: bounded_int(
        value, minimum=0, maximum=100, option="--required-approvals"), default=0)
    source_prepare = sub.add_parser("agent-source-prepare")
    source_prepare.add_argument("workspace")
    source_prepare.add_argument("--agent-id", required=True)
    source_prepare.add_argument("--name", required=True)
    source_prepare.add_argument("--repository-id", default="")
    source_prepare.add_argument("--default-branch", default="main")
    source_prepare.add_argument("--ref", default="",
                                help="explicit Commit, Tag, or Branch baseline")
    source_prepare.add_argument("--use-local", action="store_true",
                                help="preserve the current dirty local workspace as source")
    source_prepare.add_argument("--credential-ttl", type=credential_ttl, default=900)
    source_prepare.add_argument("--credential-max-uses", type=credential_max_uses,
                                default=20)
    source_prepare.add_argument("--timeout", type=float, default=900)
    source_push = sub.add_parser("agent-source-push")
    source_push.add_argument("workspace")
    source_push.add_argument("--agent-id", required=True)
    source_push.add_argument("--repository-id", required=True)
    source_push.add_argument("--path", action="append", required=True,
                             help="one workspace-relative validated Package directory; its contents are mapped to Repository root")
    source_push.add_argument("--message", required=True)
    source_push.add_argument("--branch", default="")
    source_push.add_argument("--credential-ttl", type=credential_ttl, default=900)
    source_push.add_argument("--credential-max-uses", type=credential_max_uses,
                             default=20)
    source_push.add_argument("--timeout", type=float, default=900)
    for command in ("git-clone", "git-push"):
        git_command = sub.add_parser(command)
        git_command.add_argument("repository_id")
        git_command.add_argument("--commit-sha", required=True)
        git_command.add_argument("--credential-ttl", type=credential_ttl, default=900)
        git_command.add_argument("--credential-max-uses", type=credential_max_uses,
                                 default=20)
        git_command.add_argument("--timeout", type=float, default=900)
        if command == "git-clone":
            git_command.add_argument("destination")
        else:
            git_command.add_argument("workspace")
            git_command.add_argument("--branch", required=True)
    source_revision = sub.add_parser("source-revision")
    source_revision.add_argument("repository_id")
    source_revision.add_argument("commit_sha")
    source_revision.add_argument("--pull-request-number", type=pull_request_number,
                                 required=True)
    source_revision_status = sub.add_parser("source-revision-status")
    source_revision_status.add_argument("source_revision_id")
    source_build = sub.add_parser(
        "source-build", help="submit a Build for a fixed Source Revision")
    source_build.add_argument("source_revision_id")
    source_build_status = sub.add_parser(
        "source-build-status", help="read an existing Source Revision Build")
    source_build_status.add_argument("build_id")
    source_eval = sub.add_parser(
        "source-eval", help="submit an Eval for a fixed Source Revision")
    source_eval.add_argument("source_revision_id")
    source_eval_status = sub.add_parser(
        "source-eval-status", help="read an existing Source Revision Eval")
    source_eval_status.add_argument("eval_run_id")
    agent_release = sub.add_parser(
        "agent-release", help="publish through the unified Core/Forge/Drive transaction")
    agent_release.add_argument("--agent-id", required=True)
    agent_release.add_argument("--version", required=True)
    agent_release.add_argument("--source-ref", required=True)
    agent_release.add_argument("--source-revision-id", required=True)
    agent_release.add_argument("--source-commit-sha", required=True, type=full_commit_sha)
    agent_release.add_argument("--pull-request-number", type=pull_request_number)
    agent_release.add_argument("--required-approvals", type=lambda value: bounded_int(
        value, minimum=0, maximum=100, option="--required-approvals"), default=0)
    agent_release.add_argument("--release-notes", default="")
    agent_release.add_argument("--visibility", choices=("private", "public"), default="private")
    save_forge_checkpoint = sub.add_parser("save-forge-checkpoint")
    save_forge_checkpoint.add_argument("--path", default=".agentour/forge-checkpoint.json")
    save_forge_checkpoint.add_argument("--repository-id", required=True)
    save_forge_checkpoint.add_argument("--commit-sha", required=True)
    save_forge_checkpoint.add_argument("--remote-job-id", default="")
    save_forge_checkpoint.add_argument("--contract-version", default=FORGE_CONTRACT_VERSION)
    save_forge_checkpoint.add_argument("--stage", choices=sorted(FORGE_CHECKPOINT_STAGES), required=True)
    restore_forge_checkpoint = sub.add_parser("restore-forge-checkpoint")
    restore_forge_checkpoint.add_argument("--path", default=".agentour/forge-checkpoint.json")
    restore_forge_checkpoint.add_argument("--current-commit-sha", default="")
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
    create_task.add_argument("--state-file", default="")
    update_task = sub.add_parser("update-compiler-task")
    update_task.add_argument("task_id")
    update_task.add_argument("--stage")
    update_task.add_argument("--status", choices=("active", "blocked", "completed", "cancelled"))
    update_task.add_argument("--state", default="{}")
    update_task.add_argument("--state-file", default="")
    update_task.add_argument("--package-hash")
    update_task.add_argument("--expected-revision", type=int)
    checkpoint = sub.add_parser("checkpoint-package")
    checkpoint.add_argument("task_id")
    checkpoint.add_argument("package")
    restore = sub.add_parser("restore-checkpoint")
    restore.add_argument("task_id")
    restore.add_argument("destination")
    references=sub.add_parser("upload-references")
    references.add_argument("--agent-id", required=True)
    references.add_argument("--repository-id", required=True)
    references.add_argument("files",nargs="+")
    resolve_update = sub.add_parser("resolve-update-intent")
    resolve_update.add_argument("target")
    fix_tasks = sub.add_parser("fix-tasks")
    fix_tasks.add_argument("--kind", choices=("platform", "agent", "plugin"), default="")
    claim_fix = sub.add_parser("claim-fix")
    claim_fix.add_argument("task_id")
    claim_fix.add_argument("--lease-seconds", type=int, default=1800)
    complete_fix = sub.add_parser("complete-fix")
    complete_fix.add_argument("task_id")
    complete_fix.add_argument("--result", required=True)
    args = parser.parse_args()
    if args.command == "platforms":
        print(json.dumps(PLATFORMS, ensure_ascii=False, indent=2))
    elif args.command == "bootstrap":
        cmd_bootstrap(args)
    elif args.command == "authorize":
        cmd_authorize(args)
    elif args.command == "account":
        cmd_account(args)
    elif args.command == "switch-account":
        cmd_switch_account(args)
    elif args.command == "models":
        cmd_models(args)
    elif args.command == "repositories":
        cmd_repositories(args)
    elif args.command == "repository-create":
        cmd_repository_create(args)
    elif args.command == "repository-status":
        cmd_repository_status(args)
    elif args.command == "repository":
        cmd_repository(args)
    elif args.command == "change-set-create":
        cmd_change_set_create(args)
    elif args.command == "pull-request-status":
        cmd_pull_request_status(args)
    elif args.command == "pull-request-merge":
        cmd_pull_request_merge(args)
    elif args.command == "agent-source-prepare":
        cmd_agent_source_prepare(args)
    elif args.command == "agent-source-push":
        cmd_agent_source_push(args)
    elif args.command == "git-clone":
        cmd_git_clone(args)
    elif args.command == "git-push":
        cmd_git_push(args)
    elif args.command == "source-revision":
        cmd_source_revision(args)
    elif args.command == "source-revision-status":
        cmd_source_revision_status(args)
    elif args.command == "source-build":
        cmd_source_build(args)
    elif args.command == "source-build-status":
        cmd_source_build_status(args)
    elif args.command == "source-eval":
        cmd_source_eval(args)
    elif args.command == "source-eval-status":
        cmd_source_eval_status(args)
    elif args.command == "agent-release":
        cmd_agent_release(args)
    elif args.command == "save-forge-checkpoint":
        cmd_save_forge_checkpoint(args)
    elif args.command == "restore-forge-checkpoint":
        cmd_restore_forge_checkpoint(args)
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
    elif args.command == "upload-references":
        cmd_upload_references(args)
    elif args.command == "resolve-update-intent":
        print(json.dumps(authenticated(args, "/v1/dev/packages/update-intents", method="POST",
                                       body={"target": args.target}),
                         ensure_ascii=False, indent=2))
    elif args.command == "fix-tasks":
        query = urllib.parse.urlencode({"kind": args.kind}) if args.kind else ""
        print(json.dumps(authenticated(args, f"/v1/dev/fix-tasks{f'?{query}' if query else ''}"),
                         ensure_ascii=False, indent=2))
    elif args.command == "claim-fix":
        task_id = urllib.parse.quote(args.task_id, safe="")
        print(json.dumps(authenticated(args, f"/v1/dev/fix-tasks/{task_id}/claim",
            method="POST", body={"lease_seconds": args.lease_seconds}), ensure_ascii=False, indent=2))
    elif args.command == "complete-fix":
        cmd_complete_fix(args)


if __name__ == "__main__":
    try:
        main()
    except GitOperationError as exc:
        print(str(exc), file=sys.stderr)
        raise
