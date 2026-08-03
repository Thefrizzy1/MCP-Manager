from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from core.atomic_json import read_json, write_json

_RECENT_LIMIT = 25
log = logging.getLogger(__name__)


def ensure_data_dir(root: Path) -> Path:
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def recent_path(root: Path) -> Path:
    return ensure_data_dir(root) / "recent.json"


def append_recent(root: Path, entry: dict[str, Any]) -> None:
    # Written on the tool-call hot path, so the naive read-truncate-write it used
    # to do could tear the file on a crash and lose entries under concurrency.
    # atomic_json is tmp+fsync+replace with a per-path lock.
    path = recent_path(root)
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = read_json(path, [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, entry)
    write_json(path, rows[:_RECENT_LIMIT])


def load_recent(root: Path) -> list[dict[str, Any]]:
    data = read_json(recent_path(root), [])
    return data if isinstance(data, list) else []
