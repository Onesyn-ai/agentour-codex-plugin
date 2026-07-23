#!/usr/bin/env python3
"""Cross-platform developer-token storage for Agentour Compiler Plugins."""

from __future__ import annotations

import getpass
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
    return f"AGENTOUR_TOKEN_{platform.upper()}"


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    path = Path("/proc/version")
    return path.exists() and "microsoft" in path.read_text(errors="ignore").lower()


def _fallback_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "agentour" / "credentials.json"


def _fallback_load() -> dict:
    path = _fallback_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _fallback_write(data: dict) -> None:
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


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
                  "linux-secret-service", "restricted-file"}:
        return forced
    if os.environ.get("CI") or os.environ.get("AGENTOUR_CREDENTIALS_ENV_ONLY") == "1":
        return "environment"
    if sys.platform == "win32" or _is_wsl():
        return "windows-credential-manager" if _powershell() else "restricted-file"
    if sys.platform == "darwin":
        return "macos-keychain" if shutil.which("security") else "restricted-file"
    if shutil.which("secret-tool") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return "linux-secret-service"
    return "restricted-file"


def get_token(platform: str) -> str:
    platform = _check(platform)
    from_env = os.environ.get(_env_name(platform), "").strip()
    if from_env:
        return from_env
    backend = backend_name()
    account = f"{platform}:default"
    if backend == "windows-credential-manager":
        script = f"$v=New-Object Windows.Security.Credentials.PasswordVault; try{{$c=$v.Retrieve('{SERVICE}','{account}');$c.RetrievePassword();[Console]::Out.Write($c.Password)}}catch{{exit 1}}"
        result = _ps(script)
        value = result.stdout.strip() if result.returncode == 0 else ""
        return value or str(_fallback_load().get(platform, "")).strip()
    if backend == "macos-keychain":
        result = subprocess.run(["security", "find-generic-password", "-s", SERVICE,
                                 "-a", account, "-w"], text=True, capture_output=True)
        value = result.stdout.strip() if result.returncode == 0 else ""
        return value or str(_fallback_load().get(platform, "")).strip()
    if backend == "linux-secret-service":
        result = subprocess.run(["secret-tool", "lookup", "service", SERVICE,
                                 "account", account], text=True, capture_output=True)
        value = result.stdout.strip() if result.returncode == 0 else ""
        return value or str(_fallback_load().get(platform, "")).strip()
    return str(_fallback_load().get(platform, "")).strip()


def set_token(platform: str, token: str) -> str:
    platform = _check(platform)
    token = token.strip()
    if not token.startswith("at_"):
        raise ValueError("developer token must start with at_")
    backend = backend_name()
    account = f"{platform}:default"
    if backend == "environment":
        raise RuntimeError(f"set {_env_name(platform)} in this non-interactive environment")
    if backend == "windows-credential-manager":
        script = f"$v=New-Object Windows.Security.Credentials.PasswordVault; try{{$old=$v.Retrieve('{SERVICE}','{account}');$v.Remove($old)}}catch{{}};$v.Add((New-Object Windows.Security.Credentials.PasswordCredential('{SERVICE}','{account}',$env:AGENTOUR_CREDENTIAL_VALUE)))"
        result = _ps(script, token=token)
        if result.returncode != 0:
            data = _fallback_load(); data[platform] = token; _fallback_write(data)
            return "restricted-file"
    elif backend == "macos-keychain":
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", account],
                       capture_output=True)
        result = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE,
                                 "-a", account, "-w", token], text=True, capture_output=True)
        if result.returncode != 0:
            data = _fallback_load(); data[platform] = token; _fallback_write(data)
            return "restricted-file"
    elif backend == "linux-secret-service":
        result = subprocess.run(["secret-tool", "store", "--label", "Agentour developer token",
                                 "service", SERVICE, "account", account], input=token,
                                text=True, capture_output=True)
        if result.returncode != 0:
            data = _fallback_load(); data[platform] = token; _fallback_write(data)
            return "restricted-file"
    else:
        data = _fallback_load(); data[platform] = token; _fallback_write(data)
    return backend


def delete_token(platform: str) -> None:
    platform = _check(platform); backend = backend_name(); account = f"{platform}:default"
    if backend == "windows-credential-manager":
        _ps(f"$v=New-Object Windows.Security.Credentials.PasswordVault; try{{$c=$v.Retrieve('{SERVICE}','{account}');$v.Remove($c)}}catch{{}}")
    elif backend == "macos-keychain":
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", account], capture_output=True)
    elif backend == "linux-secret-service":
        subprocess.run(["secret-tool", "clear", "service", SERVICE, "account", account], capture_output=True)
    data = _fallback_load()
    if platform in data:
        data.pop(platform, None); _fallback_write(data)


def storage_status(platform: str) -> dict:
    stored = bool(get_token(platform))
    backend = backend_name()
    if stored and platform in _fallback_load():
        backend = "restricted-file"
    return {"stored": stored, "backend": backend, "path":
            str(_fallback_path()) if backend == "restricted-file" else "system-keychain"}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"status", "set", "delete", "clear"}:
        raise SystemExit("usage: credential_store.py status [platform] | set <platform> | delete <platform> | clear")
    command = sys.argv[1]
    if command == "status":
        platforms = [sys.argv[2]] if len(sys.argv) > 2 else sorted(PLATFORMS)
        print(json.dumps({p: storage_status(p) for p in platforms}, ensure_ascii=False))
    elif command == "set":
        platform = _check(sys.argv[2])
        token = sys.stdin.read().strip() if not sys.stdin.isatty() else getpass.getpass("Developer token: ")
        print(json.dumps({"stored": True, "platform": platform, "backend": set_token(platform, token)}, ensure_ascii=False))
    elif command == "delete":
        delete_token(sys.argv[2]); print(json.dumps({"deleted": True, "platform": sys.argv[2]}))
    else:
        for platform in PLATFORMS: delete_token(platform)
        print(json.dumps({"cleared": True}))


if __name__ == "__main__":
    main()
