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


def to_front(root: Path, slug: str, name: str = "") -> dict:
    """Make a model the next one the lab tests (queues it if needed). The lab
    takes the oldest queued model, so this dates it before every other row."""
    slug = (slug or "").strip().lower()
    row = queue(root, slug, name)
    data = _load(root)
    others = [r.get("added", 0) for s, r in data["models"].items() if s != slug]
    data["models"][slug]["added"] = min([row.get("added", 0)] + [a - 1 for a in others])
    write_json(_path(root), data)
    return data["models"][slug]


def next_queued(root: Path) -> dict | None:
    """Oldest queued model, or None. A model already being tested is returned
    again so an interrupted night resumes it instead of starting another."""
    rows = sorted(entries(root).values(), key=lambda r: r.get("added", 0))
    for status in ("testing", "queued"):
        for r in rows:
            if r["status"] == status:
                return r
    return None


GPU_STATES = ("queued", "testing", "done", "failed")


def report(root: Path, slug: str, text: str = "", status: str = "", pr: str = "",
           name: str = "", review: dict | None = None, gpu3060: str = "",
           update_pr: str = "", server: str = "") -> dict:
    """The lab reports progress on a model: a log line and, optionally, a new status.

    ``review`` is what the owner looks at before publishing: where the workflow
    sits (repo, branch, folder), its sample files, what was tested and what the
    critique said.
    """
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
    if gpu3060:
        # The second test, on the 12 GB card the workflows are published for. It
        # runs when that PC can be woken, often after the workflow is already live.
        if gpu3060 not in GPU_STATES:
            raise ValueError(f"gpu3060 must be one of {GPU_STATES}")
        row["gpu3060"] = {"status": gpu3060, "text": (text or "")[:400], "ts": now}
    if server:
        # The other test, on the big server. Either test may come first: whichever
        # passes opens publishing, the later one arrives as an update.
        if server not in GPU_STATES:
            raise ValueError(f"server must be one of {GPU_STATES}")
        row["server"] = {"status": server, "text": (text or "")[:400], "ts": now}
    if update_pr:
        # A follow-up pull request with the 3060 results: Publish becomes Update.
        row["update_pr"] = update_pr[:300]
        row["publish_requested"] = False
    if review:
        row["review"] = {k: review[k] for k in ("repo", "branch", "folder", "samples", "title",
                                                "tested", "summary", "critique", "not_verified")
                         if k in review}
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


def set_flag(root: Path, slug: str, key: str, value) -> None:
    data = _load(root)
    if slug in data["models"]:
        data["models"][slug][key] = value
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
