#!/usr/bin/env python3
"""Redacted append-only flight recorder for Agentour Compiler runs."""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import time
import hashlib

_SECRET_KEYS = re.compile(r"(token|secret|password|api[_-]?key|credential|authorization)", re.I)
_SECRET_VALUES = [
    re.compile(r"\bak_[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bat_[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bts_[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\be2b_[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgcr_[A-Za-z0-9._-]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"Bearer\s+[^\s\"']+", re.I),
    re.compile(r"https?://[^\s/@:]+:[^\s/@]+@", re.I),
]
_MAX_STRING = 4000
_MAX_EVENTS = 5000


def _path() -> pathlib.Path:
    override = os.environ.get("AGENTOUR_COMPILER_FLIGHT_LOG", "").strip()
    if override:
        return pathlib.Path(override).expanduser()
    # Flight data belongs to the compiler workspace, never to the Agent Package.
    # Keeping it outside cwd prevents telemetry growth from changing package hashes.
    workspace = str(pathlib.Path.cwd().resolve())
    workspace_id = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:20]
    root = pathlib.Path(os.environ.get(
        "AGENTOUR_COMPILER_STATE_HOME", pathlib.Path.home() / ".agentour" / "compiler"))
    return root.expanduser() / "flight-events" / f"{workspace_id}.json"


def redact(value, key: str = ""):
    if _SECRET_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value[:500]]
    if isinstance(value, str):
        text = value[:_MAX_STRING]
        for pattern in _SECRET_VALUES:
            text = pattern.sub("[REDACTED]", text)
        return text
    return value


def read() -> dict:
    path = _path()
    if not path.is_file():
        return {"report_schema_version": "1.0", "created_at": time.time(), "events": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"report_schema_version": "1.0", "events": []}
    except Exception:
        return {"report_schema_version": "1.0", "events": []}


def record(event_type: str, **data) -> dict:
    state = read()
    events = state.setdefault("events", [])
    event = redact({
        "sequence": len(events) + 1,
        "event_id": f"E-{len(events) + 1:03d}",
        "occurred_at": time.time(),
        "event_type": event_type,
        **data,
    })
    events.append(event)
    if len(events) > _MAX_EVENTS:
        state["events"] = events[-_MAX_EVENTS:]
        state["truncated_event_count"] = int(state.get("truncated_event_count", 0)) + 1
    state["updated_at"] = time.time()
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        temp = pathlib.Path(handle.name)
    os.chmod(temp, 0o600)
    temp.replace(path)
    return event


def record_job_sample(job_type: str, job: dict, *, poll_count: int,
                      unchanged_seconds: float, poll_interval_seconds: float) -> dict:
    report = job.get("report") or job.get("data") or {}
    return record(
        "job_sample", job_type=job_type, job_id=job.get("id") or job.get("job_id"),
        status=job.get("status"), package_hash=job.get("package_hash"),
        error=job.get("error") or report.get("error"), gates=report.get("gates", []),
        quota_chargeable=report.get("quota_chargeable"),
        heartbeat_at=report.get("heartbeat_at"), progress=report.get("smoke_progress", []),
        stage=report.get("stage"), stage_label=report.get("stage_label"),
        started_at=report.get("started_at") or report.get("created_at"),
        finished_at=report.get("finished_at"), duration_seconds=report.get("duration_seconds"),
        poll_count=poll_count, poll_interval_seconds=poll_interval_seconds,
        unchanged_status_seconds=round(unchanged_seconds, 1),
    )
