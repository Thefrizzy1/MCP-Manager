"""Persisted UI preferences: custom tags per MCP tool."""

from __future__ import annotations

import json
import logging
from pathlib import Path

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


# ── Ignored connections ───────────────────────────────────────────────────────
# Services the user has chosen to hide: they grey out, drop out of the stats, and
# are excluded from the agent connection picker. Stored as a list of service ids.

def ignored_services_path(root: Path) -> Path:
    return root / "data" / "ignored_services.json"


def load_ignored_services(root: Path) -> list[str]:
    path = ignored_services_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return sorted({str(x) for x in data if str(x).strip()})
    except Exception as exc:
        log.warning("Failed to load ignored services from %s: %s", path, exc)
    return []


def set_service_ignored(root: Path, service_id: str, ignored: bool) -> list[str]:
    (root / "data").mkdir(parents=True, exist_ok=True)
    cur = set(load_ignored_services(root))
    sid = str(service_id).strip()
    if not sid:
        return sorted(cur)
    if ignored:
        cur.add(sid)
    else:
        cur.discard(sid)
    out = sorted(cur)
    ignored_services_path(root).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
