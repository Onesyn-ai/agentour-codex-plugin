#!/usr/bin/env python3
"""Fail-closed Agentour Plugin source and installed-cache integrity verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PLUGIN = ROOT / "plugins" / "agentour-compiler"
DEFAULT_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"
INTEGRITY_RELATIVE_PATH = pathlib.Path(".codex-plugin/release-integrity.json")
REQUIRED_FILES = frozenset({
    ".codex-plugin/plugin.json",
    "guides/forge-workflow.md",
    "guides/publishing.md",
    "scripts/agentour_api.py",
    "scripts/flight_recorder.py",
    "skills/agentour-compiler/SKILL.md",
    "skills/agentour-validator/SKILL.md",
})


class ReleaseIntegrityError(ValueError):
    """The source or installed Plugin does not match the frozen release identity."""


def _json(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError(f"invalid JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseIntegrityError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: pathlib.Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ReleaseIntegrityError(f"required regular file is missing: {path}")
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        canonical = content
    else:
        canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _release_files(plugin_root: pathlib.Path) -> tuple[str, ...]:
    files = []
    for path in plugin_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(plugin_root).as_posix()
        if (relative == INTEGRITY_RELATIVE_PATH.as_posix() or
                "__pycache__" in path.parts or path.suffix == ".pyc"):
            continue
        files.append(relative)
    missing = REQUIRED_FILES - set(files)
    if missing:
        raise ReleaseIntegrityError(
            "required Plugin release files are missing: " + ", ".join(sorted(missing)))
    return tuple(sorted(files))


def _marketplace_entry(marketplace: dict, plugin_name: str) -> dict:
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        raise ReleaseIntegrityError("marketplace plugins must be a list")
    matches = [item for item in plugins if isinstance(item, dict)
               and item.get("name") == plugin_name]
    if len(matches) != 1:
        raise ReleaseIntegrityError(
            f"marketplace must contain exactly one {plugin_name!r} entry")
    return matches[0]


def source_snapshot(plugin_root: pathlib.Path, marketplace_path: pathlib.Path) -> dict:
    plugin_root = plugin_root.resolve()
    marketplace_path = marketplace_path.resolve()
    manifest = _json(plugin_root / ".codex-plugin" / "plugin.json")
    plugin_name = str(manifest.get("name") or "")
    plugin_version = str(manifest.get("version") or "")
    if not plugin_name or not plugin_version:
        raise ReleaseIntegrityError("Plugin manifest name and version are required")
    marketplace = _json(marketplace_path)
    marketplace_name = str(marketplace.get("name") or "")
    if not marketplace_name:
        raise ReleaseIntegrityError("marketplace name is required")
    entry = _marketplace_entry(marketplace, plugin_name)
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "local":
        raise ReleaseIntegrityError("Plugin marketplace source must be local")
    source_path = str(source.get("path") or "")
    resolved_source = (marketplace_path.parent.parent.parent / source_path).resolve()
    if resolved_source != plugin_root:
        raise ReleaseIntegrityError(
            "Plugin marketplace source does not resolve to the candidate Plugin")
    return {
        "schema_version": 1,
        "plugin_name": plugin_name,
        "plugin_version": plugin_version,
        "marketplace_name": marketplace_name,
        "marketplace_source": source_path,
        "files": {relative: _sha256(plugin_root / relative)
                  for relative in _release_files(plugin_root)},
    }


def write_snapshot(plugin_root: pathlib.Path, marketplace_path: pathlib.Path) -> dict:
    snapshot = source_snapshot(plugin_root, marketplace_path)
    output = plugin_root.resolve() / INTEGRITY_RELATIVE_PATH
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2,
                                 sort_keys=True) + "\n", encoding="utf-8")
    return snapshot


def verify_source(plugin_root: pathlib.Path, marketplace_path: pathlib.Path) -> dict:
    expected = _json(plugin_root.resolve() / INTEGRITY_RELATIVE_PATH)
    actual = source_snapshot(plugin_root, marketplace_path)
    if expected != actual:
        raise ReleaseIntegrityError(
            "Plugin release integrity snapshot does not match source; regenerate it only "
            "after the final version and critical files are frozen")
    return actual


def verify_cache(plugin_root: pathlib.Path, marketplace_path: pathlib.Path,
                 cache_root: pathlib.Path) -> dict:
    expected = verify_source(plugin_root, marketplace_path)
    installed = (cache_root.expanduser().resolve() / expected["marketplace_name"] /
                 expected["plugin_name"] / expected["plugin_version"])
    if installed.is_symlink() or not installed.is_dir():
        raise ReleaseIntegrityError(
            f"installed Plugin cache for {expected['plugin_version']} is missing: {installed}")
    if (_sha256(installed / INTEGRITY_RELATIVE_PATH) !=
            _sha256(plugin_root.resolve() / INTEGRITY_RELATIVE_PATH)):
        raise ReleaseIntegrityError(
            "installed Plugin release integrity snapshot does not match source")
    manifest = _json(installed / ".codex-plugin" / "plugin.json")
    if (manifest.get("name") != expected["plugin_name"] or
            manifest.get("version") != expected["plugin_version"]):
        raise ReleaseIntegrityError("installed Plugin manifest identity does not match source")
    expected_files = set(expected["files"])
    installed_files = set(_release_files(installed))
    if installed_files != expected_files:
        missing = sorted(expected_files - installed_files)
        extra = sorted(installed_files - expected_files)
        raise ReleaseIntegrityError(
            "installed Plugin cache file set differs from source; "
            f"missing={missing}, extra={extra}")
    mismatches = [relative for relative, digest in expected["files"].items()
                  if _sha256(installed / relative) != digest]
    if mismatches:
        raise ReleaseIntegrityError(
            "installed Plugin cache differs from source: " + ", ".join(mismatches))
    return {**expected, "installed_path": str(installed)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("write", "verify-source", "verify-cache"))
    parser.add_argument("--plugin-root", type=pathlib.Path, default=DEFAULT_PLUGIN)
    parser.add_argument("--marketplace-path", type=pathlib.Path,
                        default=DEFAULT_MARKETPLACE)
    parser.add_argument("--cache-root", type=pathlib.Path,
                        default=pathlib.Path.home() / ".codex" / "plugins" / "cache")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "write":
            result = write_snapshot(args.plugin_root, args.marketplace_path)
        elif args.command == "verify-source":
            result = verify_source(args.plugin_root, args.marketplace_path)
        else:
            result = verify_cache(args.plugin_root, args.marketplace_path,
                                  args.cache_root)
    except ReleaseIntegrityError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    summary = {
        "verified": True,
        "plugin_name": result["plugin_name"],
        "plugin_version": result["plugin_version"],
        "marketplace_name": result["marketplace_name"],
        "file_count": len(result["files"]),
        **({"installed_path": result["installed_path"]}
           if "installed_path" in result else {}),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
