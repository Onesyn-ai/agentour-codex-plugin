#!/usr/bin/env python3
"""Cross-platform OAuth credential storage for Agentour Compiler Plugins."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SERVICE = "agentour-compiler"
PLATFORMS = {"test", "production"}
WINDOWS_BACKEND = "windows-credential-manager"


class CredentialStoreError(RuntimeError):
    """Stable, secret-free operating-system credential store failure."""

    def __init__(self, code: str, operation: str, detail: str = "unknown"):
        self.code = code
        self.operation = operation
        self.detail = detail if detail.replace(".", "").isalnum() else "unknown"
        super().__init__(
            f"{code}: Windows PasswordVault credential {operation} failed ({self.detail})")


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
    # PasswordVault's inbox WinRT projection is exposed by Windows PowerShell 5.1.
    # A caller running in pwsh 7 still uses this desktop bridge; falling back to
    # pwsh itself would fail type activation on modern .NET and must fail closed.
    return shutil.which("powershell.exe") or shutil.which("powershell")


def backend_name() -> str:
    forced = os.environ.get("AGENTOUR_CREDENTIAL_BACKEND", "").strip()
    if forced in {"environment", WINDOWS_BACKEND, "macos-keychain",
                  "linux-secret-service"}:
        return forced
    if os.environ.get("CI") or os.environ.get("AGENTOUR_CREDENTIALS_ENV_ONLY") == "1":
        return "environment"
    if sys.platform == "win32" or _is_wsl():
        return WINDOWS_BACKEND if _powershell() else "unavailable"
    if sys.platform == "darwin":
        return "macos-keychain" if shutil.which("security") else "unavailable"
    if shutil.which("secret-tool") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return "linux-secret-service"
    return "unavailable"


def _windows_env(platform: str) -> dict[str, str]:
    return {
        "AGENTOUR_CREDENTIAL_RESOURCE": SERVICE,
        "AGENTOUR_CREDENTIAL_ACCOUNT": f"{platform}:default",
    }


_POWERSHELL_PREAMBLE = (
    "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
    "$vaultType=[Windows.Security.Credentials.PasswordVault,"
    "Windows.Security.Credentials,ContentType=WindowsRuntime];"
    "$credentialType=[Windows.Security.Credentials.PasswordCredential,"
    "Windows.Security.Credentials,ContentType=WindowsRuntime];"
    "$vault=[Activator]::CreateInstance($vaultType);")


def _powershell_error(result: subprocess.CompletedProcess) -> str:
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    return lines[-1] if lines else "unknown"


def _ps(script: str, *, token: str | None = None,
        extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    executable = _powershell()
    if not executable:
        raise CredentialStoreError(
            "CREDENTIAL_STORE_UNAVAILABLE", "open", "PowerShellUnavailable")
    env = os.environ.copy()
    if token is not None:
        env["AGENTOUR_CREDENTIAL_VALUE"] = token
    if extra_env:
        env.update(extra_env)
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive",
         "-EncodedCommand", encoded],
        text=True, encoding="utf-8", errors="replace", capture_output=True, env=env)


def _get_secret(platform: str) -> str:
    platform = _check(platform)
    from_env = os.environ.get(_env_name(platform), "").strip()
    if from_env:
        return from_env
    backend = backend_name()
    account = f"{platform}:default"
    if backend == WINDOWS_BACKEND:
        script = (_POWERSHELL_PREAMBLE +
                  "try{$credential=$vault.Retrieve($env:AGENTOUR_CREDENTIAL_RESOURCE,"
                  "$env:AGENTOUR_CREDENTIAL_ACCOUNT);$credential.RetrievePassword();"
                  "[Console]::Out.Write($credential.Password)}catch{$errorValue=$_.Exception;"
                  "$inner=$errorValue.InnerException;if($errorValue.HResult -eq -2147023728 "
                  "-or ($null -ne $inner -and $inner.HResult -eq -2147023728)){exit 2};"
                  "[Console]::Error.Write($errorValue.GetType().FullName);exit 1}")
        result = _ps(script, extra_env=_windows_env(platform))
        if result.returncode == 2:
            return ""
        if result.returncode != 0:
            raise CredentialStoreError("CREDENTIAL_STORE_READ_FAILED", "read",
                                       _powershell_error(result))
        return result.stdout
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
    if backend == WINDOWS_BACKEND:
        script = (_POWERSHELL_PREAMBLE +
                  "try{$credential=[Activator]::CreateInstance($credentialType,[object[]]@("
                  "$env:AGENTOUR_CREDENTIAL_RESOURCE,$env:AGENTOUR_CREDENTIAL_ACCOUNT,"
                  "$env:AGENTOUR_CREDENTIAL_VALUE));$vault.Add($credential)}catch{"
                  "[Console]::Error.Write($_.Exception.GetType().FullName);exit 1}")
        result = _ps(script, token=value, extra_env=_windows_env(platform))
        if result.returncode != 0:
            raise CredentialStoreError("CREDENTIAL_STORE_WRITE_FAILED", "write",
                                       _powershell_error(result))
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
    return _set_secret(platform,json.dumps(
        credentials,ensure_ascii=False,separators=(",",":"),sort_keys=True))


def set_tenant_credentials(platform: str, credentials: dict) -> str:
    value={**credentials,"credential_type":"tenant_access_token_v1"}
    return set_credentials(platform,value)


def delete_credentials(platform: str) -> None:
    platform = _check(platform); backend = backend_name(); account = f"{platform}:default"
    if backend == WINDOWS_BACKEND:
        script = (_POWERSHELL_PREAMBLE +
                  "try{$credential=$vault.Retrieve($env:AGENTOUR_CREDENTIAL_RESOURCE,"
                  "$env:AGENTOUR_CREDENTIAL_ACCOUNT);$vault.Remove($credential)}catch{"
                  "$errorValue=$_.Exception;$inner=$errorValue.InnerException;"
                  "if($errorValue.HResult -eq -2147023728 -or ($null -ne $inner -and "
                  "$inner.HResult -eq -2147023728)){exit 0};"
                  "[Console]::Error.Write($errorValue.GetType().FullName);exit 1}")
        result = _ps(script, extra_env=_windows_env(platform))
        if result.returncode != 0:
            raise CredentialStoreError("CREDENTIAL_STORE_DELETE_FAILED", "delete",
                                       _powershell_error(result))
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
