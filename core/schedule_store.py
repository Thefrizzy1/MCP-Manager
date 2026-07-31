"""Persistent store for scheduled jobs — the "schedule anything" data layer.

A schedule runs either an **agent** task (a headless Claude Code prompt) or a
**tool** call (any Plutus MCP tool with fixed params) on a cron expression. The
APScheduler runtime that actually fires them lives in core/scheduler.py; this
module is the pure, unit-tested CRUD + validation layer, persisted to
data/schedules.json.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

VALID_KINDS = ("agent", "tool", "task")

# Structural 5-field cron check (offline). APScheduler does the authoritative
# parse when a job is scheduled; this catches obvious mistakes early.
_CRON_FIELD = re.compile(r"^[\d*/,\-]+$")


def validate_cron(expr: str) -> tuple[bool, str]:
    parts = (expr or "").split()
    if len(parts) != 5:
        return False, "Cron must have exactly 5 fields (min hour day month weekday), e.g. '0 3 * * *'."
    for i, field in enumerate(parts):
        if not _CRON_FIELD.match(field):
            return False, f"Invalid characters in cron field {i + 1}: {field!r}."
    return True, ""


def _path(root: Path) -> Path:
    return root / "data" / "schedules.json"


def load_schedules(root: Path) -> list[dict]:
    p = _path(root)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.get("schedules", []) if isinstance(data, dict) else []
        return items if isinstance(items, list) else []
    except Exception:
        return []


def save_schedules(root: Path, schedules: list[dict]) -> None:
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schedules": schedules}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _normalize(entry: dict) -> dict:
    kind = entry.get("kind")
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    cron = str(entry.get("cron", "")).strip()
    ok, err = validate_cron(cron)
    if not ok:
        raise ValueError(err)
    payload = entry.get("payload") or {}
    if kind == "agent":
        if not str(payload.get("prompt", "")).strip():
            raise ValueError("agent schedule requires payload.prompt")
    elif kind == "tool":
        if not str(payload.get("tool", "")).strip():
            raise ValueError("tool schedule requires payload.tool")
        if not isinstance(payload.get("params", {}), dict):
            raise ValueError("payload.params must be an object")
    elif kind == "task":
        if not str(payload.get("task_id", "")).strip():
            raise ValueError("task schedule requires payload.task_id")
    return {
        "id": entry.get("id") or uuid.uuid4().hex[:12],
        "name": (entry.get("name") or "").strip() or f"{kind} schedule",
        "kind": kind,
        "cron": cron,
        "timezone": (entry.get("timezone") or "Europe/Berlin").strip(),
        "enabled": bool(entry.get("enabled", True)),
        "created": entry.get("created") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # What happened the last time this fired. Without it a schedule that never
        # ran and one that ran and failed look identical in the UI — which is
        # exactly the position a user is in when they ask "did that job run?".
        "last_run": entry.get("last_run") or "",
        "last_status": entry.get("last_status") or "",
        "last_detail": (entry.get("last_detail") or "")[:300],
        "payload": payload,
    }


def record_run(root: Path, sid: str, status: str, detail: str = "") -> None:
    """Stamp a schedule with the outcome of a firing. Never raises.

    Called from the scheduler thread, so a failure to write must not take the job
    down with it — the run mattering more than the bookkeeping.
    """
    try:
        items = load_schedules(root)
        for i, it in enumerate(items):
            if it.get("id") == sid:
                items[i] = {**it,
                            "last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "last_status": status,
                            "last_detail": (detail or "")[:300]}
                save_schedules(root, items)
                return
    except Exception:
        pass


def add_schedule(root: Path, entry: dict) -> dict:
    norm = _normalize(entry)
    items = load_schedules(root)
    items.append(norm)
    save_schedules(root, items)
    return norm


def update_schedule(root: Path, sid: str, updates: dict) -> dict:
    items = load_schedules(root)
    for i, it in enumerate(items):
        if it.get("id") == sid:
            merged = {**it, **updates, "id": sid, "created": it.get("created")}
            items[i] = _normalize(merged)
            save_schedules(root, items)
            return items[i]
    raise KeyError(f"schedule {sid} not found")


def delete_schedule(root: Path, sid: str) -> bool:
    items = load_schedules(root)
    kept = [it for it in items if it.get("id") != sid]
    if len(kept) == len(items):
        return False
    save_schedules(root, kept)
    return True


def get_schedule(root: Path, sid: str) -> dict | None:
    return next((it for it in load_schedules(root) if it.get("id") == sid), None)
