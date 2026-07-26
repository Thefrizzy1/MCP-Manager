"""Global tool exposure — the "slicer".

Purely a token-optimisation layer: it decides which tool CATEGORIES the main
``/mcp`` endpoint serves. It never changes what a tool does — only how many tool
schemas ride along in every request's manifest (which is real prompt tokens).

Fail-safe by construction: disabling a category means those tools are simply not
registered on the served ``/mcp`` instance (same mechanism as profiles), so there
is nothing to bypass. The UI always still sees the full surface; only the served
manifest shrinks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.profiles import ALWAYS_EXPOSED, TOOL_CATEGORIES, infer_tool_categories

EXPOSURE_FILE = "tool_exposure.json"
_V = 1


def exposure_path(root: Path) -> Path:
    return root / "data" / EXPOSURE_FILE


def load_exposure(root: Path) -> dict[str, Any]:
    p = exposure_path(root)
    valid = set(TOOL_CATEGORIES)
    disabled: list[str] = []
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                disabled = [
                    str(x).strip().lower()
                    for x in (data.get("disabled_categories") or [])
                    if str(x).strip().lower() in valid
                ]
        except (json.JSONDecodeError, OSError):
            pass
    return {"_v": _V, "disabled_categories": sorted(set(disabled))}


def save_exposure(root: Path, disabled_categories: list[str]) -> dict[str, Any]:
    valid = set(TOOL_CATEGORIES)
    dc = sorted({str(x).strip().lower() for x in (disabled_categories or []) if str(x).strip().lower() in valid})
    p = exposure_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"_v": _V, "disabled_categories": dc}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return {"_v": _V, "disabled_categories": dc}


def is_tool_exposed(name: str, disabled: set[str]) -> bool:
    """A tool stays exposed unless *every* category it belongs to is disabled.
    Always-exposed meta tools and uncategorised utilities never get sliced out."""
    if name in ALWAYS_EXPOSED:
        return True
    cats = infer_tool_categories(name)
    if not cats:
        return True
    return bool(cats - disabled)


def resolve_exposed(root: Path, all_names: list[str]) -> set[str] | None:
    """Allowed tool-name set for the served ``/mcp``, or None when nothing is
    disabled (so the caller can reuse the prebuilt full instance)."""
    disabled = set(load_exposure(root)["disabled_categories"])
    if not disabled:
        return None
    return {n for n in all_names if is_tool_exposed(n, disabled)}


def _tool_token_estimate(tool: Any) -> int:
    """Rough prompt-token cost of a tool's manifest entry (~4 chars/token)."""
    schema = getattr(tool, "parameters", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None) or {}
    blob = json.dumps(
        {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", "") or "",
            "inputSchema": schema,
        },
        separators=(",", ":"),
        default=str,
    )
    return max(1, len(blob) // 4)


def exposure_report(root: Path, tool_manager: Any) -> dict[str, Any]:
    """Everything the Dashboard optimisation panel needs: per-category tool/token
    counts, current disabled set, and the estimated token saving."""
    tools = list(tool_manager.list_tools())
    disabled = set(load_exposure(root)["disabled_categories"])
    per = {t.name: _tool_token_estimate(t) for t in tools}
    total_tokens = sum(per.values())
    exposed = [t for t in tools if is_tool_exposed(t.name, disabled)]
    exposed_tokens = sum(per[t.name] for t in exposed)

    categories = {}
    for cat in TOOL_CATEGORIES:
        members = [t for t in tools if cat in infer_tool_categories(t.name)]
        categories[cat] = {
            "tools": len(members),
            "tokens": sum(per[t.name] for t in members),
            "enabled": cat not in disabled,
        }

    return {
        "categories": categories,
        "disabled_categories": sorted(disabled),
        "total_tools": len(tools),
        "exposed_tools": len(exposed),
        "total_tokens_est": total_tokens,
        "exposed_tokens_est": exposed_tokens,
        "tokens_saved_est": total_tokens - exposed_tokens,
        "percent_saved": round((total_tokens - exposed_tokens) / total_tokens * 100) if total_tokens else 0,
        "restart_note": "Category changes take effect after the MCP server restarts.",
    }
