"""Persisted UI preferences: custom tags per MCP tool."""

from __future__ import annotations

import logging
from pathlib import Path

from core.atomic_json import read_json, write_json

log = logging.getLogger(__name__)


def tag_overrides_path(root: Path) -> Path:
    return root / "data" / "tool_tag_overrides.json"


def load_tag_overrides(root: Path) -> dict[str, str]:
    data = read_json(tag_overrides_path(root), {})
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {}


def save_tag_override(root: Path, tool_name: str, tag: str) -> None:
    cur = load_tag_overrides(root)
    tag = tag.strip()
    if not tag:
        cur.pop(tool_name, None)
    else:
        cur[tool_name] = tag
    write_json(tag_overrides_path(root), cur)


# ── Ignored connections ───────────────────────────────────────────────────────
# Services the user has chosen to hide: they grey out, drop out of the stats, and
# are excluded from the agent connection picker. Stored as a list of service ids.

def ignored_services_path(root: Path) -> Path:
    return root / "data" / "ignored_services.json"


def load_ignored_services(root: Path) -> list[str]:
    data = read_json(ignored_services_path(root), [])
    if isinstance(data, list):
        return sorted({str(x) for x in data if str(x).strip()})
    return []


def set_service_ignored(root: Path, service_id: str, ignored: bool) -> list[str]:
    cur = set(load_ignored_services(root))
    sid = str(service_id).strip()
    if not sid:
        return sorted(cur)
    if ignored:
        cur.add(sid)
    else:
        cur.discard(sid)
    out = sorted(cur)
    write_json(ignored_services_path(root), out)
    return out
