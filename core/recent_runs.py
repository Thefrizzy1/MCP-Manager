from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

_RECENT_LIMIT = 25
log = logging.getLogger(__name__)


def ensure_data_dir(root: Path) -> Path:
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def recent_path(root: Path) -> Path:
    return ensure_data_dir(root) / "recent.json"


def append_recent(root: Path, entry: dict[str, Any]) -> None:
    path = recent_path(root)
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except Exception as exc:
            log.warning("Failed to load recent runs from %s: %s", path, exc)
            rows = []
    rows.insert(0, entry)
    rows = rows[:_RECENT_LIMIT]
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def load_recent(root: Path) -> list[dict[str, Any]]:
    path = recent_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning("Failed to read recent runs from %s: %s", path, exc)
        return []
