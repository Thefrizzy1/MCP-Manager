"""Beta: cache last tool outputs for dashboard inspection + optional background refresh."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable  # Any: tool_manager duck-typed

from core.invoke_tool import invoke_mcp_tool_fn
from core.result_status import text_looks_successful
from core.tool_gate import load_gate
from core.tool_registry import is_tool_environment_ready, merged_smoke_payload

PREFS_NAME = "beta_tool_cache_prefs.json"
ENTRIES_NAME = "beta_tool_cache_entries.json"

DEFAULT_PREFS: dict[str, Any] = {
    "enabled": False,
    "refresh_hours": 5.0,
    "refresh_scope": "all",
    "disabled_service_ids": [],
    "disabled_tool_names": [],
    "last_refresh_started": None,
    "last_refresh_finished": None,
    "last_refresh_unix": 0.0,
    "last_error": None,
}

MAX_ENTRY_CHARS = 24_000
log = logging.getLogger(__name__)


def _prefs_path(root: Path) -> Path:
    return root / "data" / PREFS_NAME


def _entries_path(root: Path) -> Path:
    return root / "data" / ENTRIES_NAME


def load_prefs(root: Path) -> dict[str, Any]:
    p = _prefs_path(root)
    if not p.is_file():
        return dict(DEFAULT_PREFS)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_PREFS)
    out = dict(DEFAULT_PREFS)
    if isinstance(raw, dict):
        out.update(raw)
    return out


def save_prefs(root: Path, prefs: dict[str, Any]) -> None:
    p = _prefs_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_PREFS)
    merged.update(prefs)
    p.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


def load_entries(root: Path) -> dict[str, Any]:
    p = _entries_path(root)
    if not p.is_file():
        return {"by_tool": {}, "updated": None}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"by_tool": {}, "updated": None}
    if not isinstance(raw, dict):
        return {"by_tool": {}, "updated": None}
    raw.setdefault("by_tool", {})
    return raw


def save_entries(root: Path, data: dict[str, Any]) -> None:
    p = _entries_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def record_tool_output(root: Path, tool_name: str, text: str, *, ok: bool) -> None:
    """Merge one tool result (e.g. after dashboard /tool/run)."""
    data = load_entries(root)
    by = data.setdefault("by_tool", {})
    snippet = (text or "")[:MAX_ENTRY_CHARS]
    by[tool_name] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": ok,
        "chars": len(snippet),
        "preview": snippet[:400].replace("\r\n", "\n"),
        "output": snippet,
    }
    data["updated"] = by[tool_name]["ts"]
    save_entries(root, data)


async def refresh_all_cached_tools(
    root: Path,
    tool_manager: Any,
    services: list[dict],
    *,
    prefs_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prefs = prefs_override if prefs_override is not None else load_prefs(root)
    gate = load_gate(root)
    disabled_s = {str(x).strip().lower() for x in (prefs.get("disabled_service_ids") or []) if str(x).strip()}
    disabled_t = {str(x).strip() for x in (prefs.get("disabled_tool_names") or []) if str(x).strip()}
    disabled_t |= set(gate.get("disabled_tools") or [])
    gate_sec = {str(x).strip().lower() for x in (gate.get("disabled_sections") or []) if str(x).strip()}
    scope = str(prefs.get("refresh_scope") or "all").strip().lower()
    entries = load_entries(root)
    by_tool = entries.setdefault("by_tool", {})
    ran = skipped = fail_n = 0
    prefs_mut = dict(prefs)
    prefs_mut["last_refresh_started"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prefs_mut["last_error"] = None
    save_prefs(root, prefs_mut)

    for svc in services:
        sid = str(svc.get("id") or "").strip().lower()
        sec = str(svc.get("section") or "").strip().lower()
        if sid in disabled_s:
            continue
        if sec in gate_sec:
            continue
        if scope == "public_apis" and sec != "public":
            continue
        if scope == "selfhosted_only" and sec != "selfhosted":
            continue
        for tdef in svc.get("tools") or []:
            tn = tdef.get("name")
            if not tn or tn in disabled_t:
                skipped += 1
                continue
            if scope == "information" and not str(tn).startswith("pub_"):
                skipped += 1
                continue
            tool = tool_manager.get_tool(str(tn))
            if not tool:
                skipped += 1
                continue
            if not is_tool_environment_ready(tn):
                skipped += 1
                continue
            payload = merged_smoke_payload(str(tn))
            try:
                out = await asyncio.wait_for(invoke_mcp_tool_fn(tool.fn, payload=payload), timeout=55.0)
                text = "" if out is None else str(out)
                ok = text_looks_successful(text)
                snippet = text[:MAX_ENTRY_CHARS]
                by_tool[str(tn)] = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "ok": ok,
                    "chars": len(snippet),
                    "preview": snippet[:400].replace("\r\n", "\n"),
                    "output": snippet,
                    "service_id": sid,
                }
                ran += 1
                if not ok:
                    fail_n += 1
            except Exception as e:
                by_tool[str(tn)] = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "ok": False,
                    "chars": 0,
                    "preview": str(e)[:400],
                    "output": str(e)[:MAX_ENTRY_CHARS],
                    "service_id": sid,
                    "error": type(e).__name__,
                }
                fail_n += 1
                ran += 1

    entries["by_tool"] = by_tool
    entries["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_entries(root, entries)
    prefs_mut["last_refresh_finished"] = entries["updated"]
    prefs_mut["last_refresh_unix"] = time.time()
    prefs_mut["last_error"] = None
    save_prefs(root, prefs_mut)
    return {"ran": ran, "skipped": skipped, "failed_outputs": fail_n, "updated": entries["updated"]}


_refresh_lock = asyncio.Lock()


def _should_run_scheduled_refresh(prefs: dict[str, Any]) -> bool:
    if not prefs.get("enabled"):
        return False
    hours = float(prefs.get("refresh_hours") or 5.0)
    interval = max(hours * 3600.0, 300.0)
    last = float(prefs.get("last_refresh_unix") or 0)
    if last <= 0:
        return True
    return (time.time() - last) >= interval


async def maybe_scheduled_refresh(
    root: Path,
    tool_manager: Any,
    all_services_fn: Callable[[], list[dict]],
) -> None:
    prefs = load_prefs(root)
    if not _should_run_scheduled_refresh(prefs):
        return
    async with _refresh_lock:
        prefs = load_prefs(root)
        if not _should_run_scheduled_refresh(prefs):
            return
        await refresh_all_cached_tools(root, tool_manager, all_services_fn())


async def beta_cache_background_loop(root: Path, tool_manager_ref: Callable[[], Any], services_fn: Callable[[], list[dict]]) -> None:
    """Wake periodically; run scheduled refresh when prefs enabled."""
    while True:
        await asyncio.sleep(120)
        try:
            tm = tool_manager_ref()
            await maybe_scheduled_refresh(root, tm, services_fn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Background tool cache refresh failed: %s", exc)
            prefs = load_prefs(root)
            prefs["last_error"] = f"background loop error: {type(exc).__name__}"
            save_prefs(root, prefs)
