"""core/lab_board.py — which models the lab should test, and how far each one got.

The Trend Radar finds models; this is the short list of what the model lab is
doing about them. One row per model: queued, testing, built, failed, waiting
(for core support or an input), published or skipped, plus a progress log the
lab writes as it works. Stored in ``data/lab_board.json``.
"""
from __future__ import annotations

import time
from pathlib import Path

from core.atomic_json import read_json, write_json

STATUSES = ("queued", "testing", "built", "failed", "waiting", "published", "skipped")
OPEN = ("queued", "testing")


def _path(root: Path) -> Path:
    return Path(root) / "data" / "lab_board.json"


def _load(root: Path) -> dict:
    data = read_json(_path(root), {})
    data = data if isinstance(data, dict) else {}
    data.setdefault("models", {})
    data.setdefault("heartbeat", {})
    return data


def entries(root: Path) -> dict[str, dict]:
    return _load(root)["models"]


def heartbeat(root: Path) -> dict:
    return _load(root)["heartbeat"]


def queue(root: Path, slug: str, name: str = "", note: str = "") -> dict:
    """Put a model on the lab's list (or back on it after a failure)."""
    slug = (slug or "").strip().lower()
    if not slug:
        raise ValueError("slug is required")
    data = _load(root)
    row = data["models"].get(slug) or {"slug": slug, "name": name or slug, "added": int(time.time()),
                                      "log": [], "pr": ""}
    row.update(status="queued", name=name or row["name"], updated=int(time.time()))
    if note:
        row["note"] = note[:300]
    data["models"][slug] = row
    write_json(_path(root), data)
    return row


def next_queued(root: Path) -> dict | None:
    """Oldest queued model, or None. A model already being tested is returned
    again so an interrupted night resumes it instead of starting another."""
    rows = sorted(entries(root).values(), key=lambda r: r.get("added", 0))
    for status in ("testing", "queued"):
        for r in rows:
            if r["status"] == status:
                return r
    return None


def report(root: Path, slug: str, text: str = "", status: str = "", pr: str = "",
           name: str = "") -> dict:
    """The lab reports progress on a model: a log line and, optionally, a new status."""
    slug = (slug or "").strip().lower()
    if not slug:
        raise ValueError("slug is required")
    if status and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    data = _load(root)
    now = int(time.time())
    row = data["models"].get(slug) or {"slug": slug, "name": name or slug, "added": now,
                                      "status": "testing", "log": [], "pr": ""}
    if status:
        row["status"] = status
    if pr:
        row["pr"] = pr[:300]
    if text:
        row["log"] = (row.get("log") or [])[-39:] + [{"ts": now, "text": text.strip()[:400]}]
    row["updated"] = now
    data["models"][slug] = row
    data["heartbeat"] = {"ts": now, "text": (text or status)[:200], "slug": slug}
    write_json(_path(root), data)
    return row


def beat(root: Path, text: str) -> None:
    """A sign of life from the lab that is not about one model (preflight, blocked)."""
    data = _load(root)
    data["heartbeat"] = {"ts": int(time.time()), "text": (text or "")[:200], "slug": ""}
    write_json(_path(root), data)


def set_status(root: Path, slug: str, status: str) -> dict:
    """Dashboard: skip a model, or clear it off the list (status '')."""
    data = _load(root)
    if not status:
        data["models"].pop(slug, None)
        write_json(_path(root), data)
        return {}
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    row = data["models"].get(slug) or {"slug": slug, "name": slug, "added": int(time.time()),
                                      "log": [], "pr": ""}
    row.update(status=status, updated=int(time.time()))
    data["models"][slug] = row
    write_json(_path(root), data)
    return row
