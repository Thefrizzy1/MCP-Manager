"""Persisted UI preferences: custom tags per MCP tool."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def tag_overrides_path(root: Path) -> Path:
    return root / "data" / "tool_tag_overrides.json"


def load_tag_overrides(root: Path) -> dict[str, str]:
    path = tag_overrides_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception as exc:
        log.warning("Failed to load UI tag overrides from %s: %s", path, exc)
    return {}


def save_tag_override(root: Path, tool_name: str, tag: str) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    cur = load_tag_overrides(root)
    tag = tag.strip()
    if not tag:
        cur.pop(tool_name, None)
    else:
        cur[tool_name] = tag
    tag_overrides_path(root).write_text(json.dumps(cur, indent=2), encoding="utf-8")
