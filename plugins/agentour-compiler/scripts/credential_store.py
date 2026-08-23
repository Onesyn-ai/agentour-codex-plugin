#!/usr/bin/env python3
"""Cross-platform OAuth credential storage for Agentour Compiler Plugins."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SERVICE = "agentour-compiler"
PLATFORMS = {"test", "production"}


def _check(platform: str) -> str:
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform}")
    return platform


def _env_name(platform: str) -> str:
    return f"AGENTOUR_OAUTH_BUNDLE_{platform.upper()}"


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    path = Path("/proc/version")
    return path.exists() and "microsoft" in path.read_text(errors="ignore").lower()


def _powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _ps(script: str, *, token: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if token is not None:
        env["AGENTOUR_CREDENTIAL_VALUE"] = token
    return subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                          text=True, capture_output=True, env=env)


def backend_name() -> str:
    forced = os.environ.get("AGENTOUR_CREDENTIAL_BACKEND", "").strip()
    if forced in {"environment", "windows-credential-manager", "macos-keychain",
                  "linux-secret-service"}:
        return forced
    if os.environ.get("CI") or os.environ.get("AGENTOUR_CREDENTIALS_ENV_ONLY") == "1":
        return "environment"
    if sys.platform == "win32" or _is_wsl():
        return "windows-credential-manager" if _powershell() else "unavailable"
    if sys.platform == "darwin":
        return "macos-keychain" if shutil.which("security") else "unavailable"
    if shutil.which("secret-tool") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return "linux-secret-service"
    return "unavailable"


def _get_secret(platform: str) -> str:
    platform = _check(platform)
    from_env = os.environ.get(_env_name(platform), "").strip()
    if from_env:
        return from_env
    backend = backend_name()
    account = f"{platform}:default"
    if backend == "windows-credential-manager":
        script = f"$v=New-Object Windows.Security.Credentials.PasswordVault; try{{$c=$v.Retrieve('{SERVICE}','{account}');$c.RetrievePassword();[Console]::Out.Write($c.Password)}}catch{{exit 1}}"
        result = _ps(script)
        return result.stdout.strip() if result.returncode == 0 else ""
    if backend == "macos-keychain":
        result = subprocess.run(["security", "find-generic-password", "-s", SERVICE,
                                 "-a", account, "-w"], text=True, capture_output=True)
        return result.stdout.strip() if result.returncode == 0 else ""
    if backend == "linux-secret-service":
        result = subprocess.run(["secret-tool", "lookup", "service", SERVICE,
                                 "account", account], text=True, capture_output=True)
        return result.stdout.strip() if result.returncode == 0 else ""
    return ""


def _set_secret(platform: str, value: str) -> str:
    platform = _check(platform)
    value = value.strip()
    if not value:
        raise ValueError("OAuth credential bundle must not be empty")
    backend = backend_name()
    account = f"{platform}:default"
    if backend == "environment":
        raise RuntimeError(f"set {_env_name(platform)} in this non-interactive environment")
    if backend == "windows-credential-manager":
        script = f"$v=New-Object Windows.Security.Credentials.PasswordVault; try{{$old=$v.Retrieve('{SERVICE}','{account}');$v.Remove($old)}}catch{{}};$v.Add((New-Object Windows.Security.Credentials.PasswordCredential('{SERVICE}','{account}',$env:AGENTOUR_CREDENTIAL_VALUE)))"
        result = _ps(script, token=value)
        if result.returncode != 0:
            raise RuntimeError("Windows Credential Manager is unavailable; OAuth credentials were not stored")
    elif backend == "macos-keychain":
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", account],
                       capture_output=True)
        result = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE,
                                 "-a", account, "-w", value], text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError("macOS Keychain is unavailable; OAuth credentials were not stored")
    elif backend == "linux-secret-service":
        result = subprocess.run(["secret-tool", "store", "--label", "Agentour Plugin OAuth",
                                 "service", SERVICE, "account", account], input=value,
                                text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError("Linux Secret Service is unavailable; OAuth credentials were not stored")
    else:
        raise RuntimeError("an operating-system credential store is required")
    return backend


def get_credentials(platform: str) -> dict:
    raw=_get_secret(platform)
    try:value=json.loads(raw)
    except (TypeError,json.JSONDecodeError):return {}
    if (not isinstance(value,dict) or value.get("credential_type") not in
            {"oauth_public_client_v1","tenant_access_token_v1"}):return {}
    return value


def set_credentials(platform: str, credentials: dict) -> str:
    credential_type=str(credentials.get("credential_type") or "") if isinstance(credentials,dict) else ""
    required=({"credential_type","client_id","access_token","refresh_token","expires_at",
               "issuer","subject","scopes"} if credential_type=="oauth_public_client_v1" else
              {"credential_type","access_token","expires_at","scopes","api_origin","tenant_id"})
    if not isinstance(credentials,dict) or not required.issubset(credentials):
        raise ValueError("OAuth credential bundle is incomplete")
    return _set_secret(platform,json.dumps(credentials,separators=(",",":"),sort_keys=True))


def set_tenant_credentials(platform: str, credentials: dict) -> str:
    value={**credentials,"credential_type":"tenant_access_token_v1"}
    return set_credentials(platform,value)


def delete_credentials(platform: str) -> None:
    platform = _check(platform); backend = backend_name(); account = f"{platform}:default"
    if backend == "windows-credential-manager":
        _ps(f"$v=New-Object Windows.Security.Credentials.PasswordVault; try{{$c=$v.Retrieve('{SERVICE}','{account}');$v.Remove($c)}}catch{{}}")
    elif backend == "macos-keychain":
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", account], capture_output=True)
    elif backend == "linux-secret-service":
        subprocess.run(["secret-tool", "clear", "service", SERVICE, "account", account], capture_output=True)


def storage_status(platform: str) -> dict:
    stored = bool(get_credentials(platform))
    backend = backend_name()
    return {"stored": stored, "backend": backend, "path":
            "environment" if backend == "environment" else "system-keychain"}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"status", "delete", "clear"}:
        raise SystemExit("usage: credential_store.py status [platform] | delete <platform> | clear")
    command = sys.argv[1]
    if command == "status":
        platforms = [sys.argv[2]] if len(sys.argv) > 2 else sorted(PLATFORMS)
        print(json.dumps({p: storage_status(p) for p in platforms}, ensure_ascii=False))
    elif command == "delete":
        delete_credentials(sys.argv[2]); print(json.dumps({"deleted": True, "platform": sys.argv[2]}))
    else:
        for platform in PLATFORMS: delete_credentials(platform)
        print(json.dumps({"cleared": True}))


if __name__ == "__main__":
    main()
