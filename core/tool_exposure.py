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
import re
from pathlib import Path
from typing import Any

from core.profiles import ALWAYS_EXPOSED, TOOL_CATEGORIES, infer_tool_categories

EXPOSURE_FILE = "tool_exposure.json"
_V = 1


def exposure_path(root: Path) -> Path:
    return root / "data" / EXPOSURE_FILE


_TOOL_NAME_OK = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


def load_exposure(root: Path) -> dict[str, Any]:
    p = exposure_path(root)
    valid = set(TOOL_CATEGORIES)
    disabled: list[str] = []
    tools: list[str] = []
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                disabled = [
                    str(x).strip().lower()
                    for x in (data.get("disabled_categories") or [])
                    if str(x).strip().lower() in valid
                ]
                tools = [
                    str(x).strip()
                    for x in (data.get("disabled_tools") or [])
                    if _TOOL_NAME_OK.fullmatch(str(x).strip())
                ]
        except (json.JSONDecodeError, OSError):
            pass
    return {"_v": _V, "disabled_categories": sorted(set(disabled)),
            "disabled_tools": sorted(set(tools))}


def save_exposure(root: Path, disabled_categories: list[str] | None = None, *,
                  disabled_tools: list[str] | None = None) -> dict[str, Any]:
    """Persist the exposure choice.

    Either list may be omitted, in which case the stored value is kept — the
    category slicer and the per-tool switches are edited from different screens,
    so a save from one must not silently wipe the other.
    """
    current = load_exposure(root)
    valid = set(TOOL_CATEGORIES)
    if disabled_categories is None:
        dc = current["disabled_categories"]
    else:
        dc = sorted({str(x).strip().lower() for x in disabled_categories
                     if str(x).strip().lower() in valid})
    if disabled_tools is None:
        dt = current["disabled_tools"]
    else:
        dt = sorted({str(x).strip() for x in disabled_tools
                     if _TOOL_NAME_OK.fullmatch(str(x).strip())})

    p = exposure_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    payload = {"_v": _V, "disabled_categories": dc, "disabled_tools": dt}
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return payload


# A fresh, un-tuned install serves the full ~209-tool manifest — the dominant
# per-request token cost. Seed a lean default on first boot instead: the novelty
# public APIs (jokes, trivia, crypto tickers, IP lookups — the ~58 pub_* tools in
# these three categories) are off until the operator turns them on in Settings.
# Only applied when no exposure choice exists yet; any saved choice fully governs.
DEFAULT_DISABLED_CATEGORIES: tuple[str, ...] = ("trivia", "crypto", "ip_network")


def ensure_exposure_seed(root: Path) -> bool:
    """Seed the lean default exposure on a fresh install (no file yet).

    Idempotent and non-destructive: if an exposure choice was ever saved, this
    does nothing. Returns True iff it wrote the seed. Call once at boot, before
    the MCP app resolves what to serve.
    """
    if exposure_path(root).is_file():
        return False
    save_exposure(root, list(DEFAULT_DISABLED_CATEGORIES))
    return True


def is_tool_exposed(name: str, disabled: set[str], disabled_tools: set[str] | None = None) -> bool:
    """A tool stays exposed unless it is switched off individually, or *every*
    category it belongs to is disabled.

    An explicit per-tool switch wins over the category rules — including over
    ALWAYS_EXPOSED's blanket exemption for uncategorised tools, which is what
    makes the individual public-API toggles actually take effect (most pub_* tools
    are uncategorised, so the category slicer alone can never remove them).
    ALWAYS_EXPOSED meta tools stay exempt: turning off plutus_tool_slicer would
    remove the only means of turning anything back on.
    """
    if name in ALWAYS_EXPOSED:
        return True
    if disabled_tools and name in disabled_tools:
        return False
    cats = infer_tool_categories(name)
    if not cats:
        return True
    return bool(cats - disabled)


# Below this, the served surface is not "optimised" — it is broken. Disabling
# every category once left /mcp with 21 tools and no way to write anything: agents
# reported "I don't have tools to write files" and lost their work, while the
# dashboard's own smoke tests kept passing because they run against the full
# registry rather than the served instance.
MIN_SERVED_FRACTION = 0.05


def resolve_exposed(root: Path, all_names: list[str]) -> set[str] | None:
    """Allowed tool-name set for the served ``/mcp``, or None when nothing is
    disabled (so the caller can reuse the prebuilt full instance)."""
    ex = load_exposure(root)
    disabled = set(ex["disabled_categories"])
    disabled_tools = set(ex["disabled_tools"])
    if not disabled and not disabled_tools:
        return None
    return {n for n in all_names if is_tool_exposed(n, disabled, disabled_tools)}


def exposure_warning(root: Path, all_names: list[str]) -> str:
    """A plain-language warning when the slice has gone too far, else ''.

    Surfaced in the dashboard because the failure is otherwise invisible: the
    served manifest shrinks silently and only an agent, mid-task, discovers it has
    no tools.
    """
    served = resolve_exposed(root, all_names)
    if served is None or not all_names:
        return ""
    if len(served) >= max(1, int(len(all_names) * MIN_SERVED_FRACTION)):
        return ""
    return (f"Only {len(served)} of {len(all_names)} tools are being served. Agents "
            "will report that they have no tools for most tasks. Re-enable "
            "categories under tool exposure.")


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
    ex = load_exposure(root)
    disabled = set(ex["disabled_categories"])
    disabled_tools = set(ex["disabled_tools"])
    per = {t.name: _tool_token_estimate(t) for t in tools}
    total_tokens = sum(per.values())
    # Count per-tool switches too, or the reported saving understates reality.
    exposed = [t for t in tools if is_tool_exposed(t.name, disabled, disabled_tools)]
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
        "disabled_tools": sorted(disabled_tools),
        "total_tools": len(tools),
        "exposed_tools": len(exposed),
        "total_tokens_est": total_tokens,
        "exposed_tokens_est": exposed_tokens,
        "tokens_saved_est": total_tokens - exposed_tokens,
        "percent_saved": round((total_tokens - exposed_tokens) / total_tokens * 100) if total_tokens else 0,
        "restart_note": "Category changes take effect after the MCP server restarts.",
    }
