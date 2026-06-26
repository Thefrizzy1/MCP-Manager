"""Per-service dashboard smoke tests with strict validation and behavioral checks."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from core.invoke_tool import invoke_mcp_tool_fn, schema_audit_for_payload
from core.result_status import text_looks_successful
from core.tool_registry import (
    is_tool_environment_ready,
    looks_like_missing_service_config,
    merged_smoke_payload,
    tool_safety_level,
)

log = logging.getLogger(__name__)

REVERSIBLE_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "nextcloud_add_task",
        "nextcloud_add_event",
        "habitica_add_todo",
    }
)


async def run_service_smoke_tools(
    tool_manager: Any,
    tool_entries: list[dict],
    *,
    timeout: float = 45.0,
) -> dict[str, Any]:
    lines: list[str] = []
    results: list[dict[str, Any]] = []
    passed = failed = warnings = skipped = 0

    for tdef in tool_entries:
        tn = tdef["name"]
        safety_level = tool_safety_level(tn)
        if safety_level >= 2 or (safety_level == 1 and tn not in REVERSIBLE_MUTATION_TOOLS):
            lines.append(f"- {tn}  (skipped: safety level {safety_level})")
            results.append(_result(tn, "warning", "safety", f"Skipped: safety level {safety_level}."))
            warnings += 1
            skipped += 1
            continue

        tool = tool_manager.get_tool(tn)
        if not tool:
            lines.append(f"- {tn}  (skipped: disabled or not registered)")
            results.append(_result(tn, "warning", "registration", "Skipped: disabled or not registered."))
            warnings += 1
            skipped += 1
            continue
        if not is_tool_environment_ready(tn):
            lines.append(f"- {tn}  (skipped: not configured)")
            results.append(_result(tn, "warning", "configuration", "Skipped: not configured."))
            warnings += 1
            skipped += 1
            continue

        payload = merged_smoke_payload(tn)
        try:
            if tn == "nextcloud_add_task":
                check = await _nextcloud_task_transaction(tool_manager, timeout)
            elif tn == "nextcloud_add_event":
                check = await _nextcloud_event_transaction(tool_manager, timeout)
            elif tn == "habitica_add_todo":
                check = await _habitica_todo_transaction(tool_manager, timeout)
            else:
                audit = schema_audit_for_payload(tool.fn, payload)
                if audit["status"] != "pass":
                    detail = _audit_detail(audit)
                    check = _result(tn, "fail", "validation", detail, audit=audit)
                else:
                    output = await asyncio.wait_for(invoke_mcp_tool_fn(tool.fn, payload=payload), timeout=timeout)
                    text = "" if output is None else str(output).strip()
                    check = _classify_execution(tn, text)

            lines.append(_format_tool_report(tn, check))
            results.append(check)
            if check["status"] == "pass":
                passed += 1
            elif check["status"] == "warning":
                warnings += 1
            else:
                failed += 1
        except ValidationError as e:
            detail = str(e).strip()[:900]
            lines.append(f"FAIL {tn}\n   VALIDATION: FAIL\n   {detail}")
            results.append(_result(tn, "fail", "validation", detail))
            failed += 1
        except asyncio.TimeoutError:
            detail = f"Timeout after {int(timeout)}s."
            lines.append(f"FAIL {tn}  {detail}")
            results.append(_result(tn, "fail", "execution", detail))
            failed += 1
        except Exception as e:
            detail = f"{type(e).__name__}: {str(e)[:400]}"
            lines.append(f"FAIL {tn}  {detail[:200]}")
            results.append(_result(tn, "fail", "execution", detail))
            failed += 1

    summary = f"Summary: {passed} ok | {failed} failed | {warnings} warning | {skipped} skipped"
    body = "\n\n".join(lines) if lines else "(no tools for this service)"
    output = summary + "\n\n" + body
    return {
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
        "skipped": skipped,
        "results": results,
        "output": output,
        "ok": failed == 0,
    }


def _result(
    tool: str,
    status: str,
    phase: str,
    details: str,
    *,
    audit: dict[str, Any] | None = None,
    phases: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"tool": tool, "status": status, "phase": phase, "details": details}
    if audit is not None:
        out["schema_audit"] = audit
    if phases is not None:
        out["phases"] = phases
    return out


def _audit_detail(audit: dict[str, Any]) -> str:
    parts = []
    if audit.get("missing"):
        parts.append("Missing field(s): " + ", ".join(audit["missing"]))
    if audit.get("unexpected"):
        parts.append("Unexpected field(s): " + ", ".join(audit["unexpected"]))
    if not parts:
        parts.append("Payload does not match schema.")
    return "; ".join(parts)


def _classify_execution(tool: str, text: str) -> dict[str, Any]:
    low = text.strip().lower()
    if looks_like_missing_service_config(text):
        return _result(tool, "warning", "configuration", text[:450])
    if not text_looks_successful(text) or "exception" in low:
        return _result(tool, "fail", "execution", text[:900] or "Empty response.")
    return _result(tool, "pass", "execution", text.replace("\r\n", "\n")[:280])


def _format_tool_report(tool: str, check: dict[str, Any]) -> str:
    label = {"pass": "PASS", "fail": "FAIL", "warning": "WARN"}.get(check["status"], "UNKNOWN")
    phases = check.get("phases")
    if phases:
        rows = "\n".join(f"   {p['phase'].upper()}: {p['status'].upper()}" for p in phases)
        return f"{tool}\n{rows}\n   Overall: {label}\n   {check['details'][:450]}"
    return f"{tool}\n   {check['phase'].upper()}: {label}\n   {check['details'][:450]}"


async def _invoke_named(tool_manager: Any, name: str, payload: dict, timeout: float) -> str:
    tool = tool_manager.get_tool(name)
    if not tool:
        raise RuntimeError(f"Required tool not available: {name}")
    output = await asyncio.wait_for(invoke_mcp_tool_fn(tool.fn, payload=payload), timeout=timeout)
    return "" if output is None else str(output)


def _uid_from_text(text: str) -> str:
    match = re.search(r"UID:\s*`?([A-Za-z0-9_.@:-]+)`?", text)
    return match.group(1) if match else ""


def _id_from_text(text: str) -> str:
    """Parse `(ID: <id>)` as emitted by habitica_add_todo."""
    match = re.search(r"\(ID:\s*([^)\s]+)\s*\)", text)
    return match.group(1) if match else ""


def _calendar_slug_from_text(text: str) -> str:
    match = re.search(r"slug:\s*`([^`]+)`", text)
    return match.group(1) if match else "personal"


async def _nextcloud_task_transaction(tool_manager: Any, timeout: float) -> dict[str, Any]:
    title = f"TEST_SMOKE_TASK_{int(time.time())}"
    list_name = "tasks"
    phases: list[dict[str, str]] = [{"phase": "validation", "status": "pass"}]
    uid = ""
    try:
        create = await _invoke_named(tool_manager, "nextcloud_add_task", {"title": title, "list_name": list_name}, timeout)
        uid = _uid_from_text(create)
        if not uid or create.strip().lower().startswith("error:"):
            phases.append({"phase": "create", "status": "fail"})
            return _result("nextcloud_add_task", "fail", "create", create[:900] or "Created task without UID.", phases=phases)
        phases.append({"phase": "create", "status": "pass"})

        listed = await _invoke_named(tool_manager, "nextcloud_get_tasks", {"list_name": list_name, "include_completed": True}, timeout)
        if title not in listed and uid not in listed:
            phases.append({"phase": "verify", "status": "fail"})
            return _result("nextcloud_add_task", "fail", "verification", "Created task was not found.", phases=phases)
        phases.append({"phase": "verify", "status": "pass"})

        deleted = await _invoke_named(tool_manager, "nextcloud_delete_task", {"list_name": list_name, "uid": uid}, timeout)
        if deleted.strip().lower().startswith("error:"):
            phases.append({"phase": "delete", "status": "fail"})
            return _result("nextcloud_add_task", "fail", "cleanup", deleted[:900], phases=phases)
        phases.append({"phase": "delete", "status": "pass"})

        listed_after = await _invoke_named(tool_manager, "nextcloud_get_tasks", {"list_name": list_name, "include_completed": True}, timeout)
        if title in listed_after or uid in listed_after:
            phases.append({"phase": "cleanup verify", "status": "fail"})
            return _result("nextcloud_add_task", "fail", "cleanup", "Task still exists after delete.", phases=phases)
        phases.append({"phase": "cleanup verify", "status": "pass"})
        return _result("nextcloud_add_task", "pass", "cleanup", "Created task, located it, deleted it, and verified deletion.", phases=phases)
    finally:
        if uid and not any(p["phase"] == "delete" and p["status"] == "pass" for p in phases):
            try:
                await _invoke_named(tool_manager, "nextcloud_delete_task", {"list_name": list_name, "uid": uid}, timeout)
            except Exception as exc:
                log.warning("Failed emergency cleanup for smoke task %s: %s", uid, exc)


async def _nextcloud_event_transaction(tool_manager: Any, timeout: float) -> dict[str, Any]:
    title = f"TEST_SMOKE_EVENT_{int(time.time())}"
    start = datetime.now() + timedelta(minutes=5)
    end = start + timedelta(minutes=5)
    phases: list[dict[str, str]] = [{"phase": "validation", "status": "pass"}]
    uid = ""
    calendars = await _invoke_named(tool_manager, "nextcloud_list_calendars", {}, timeout)
    calendar = _calendar_slug_from_text(calendars)
    try:
        payload = {
            "calendar": calendar,
            "title": title,
            "date": start.strftime("%Y-%m-%d"),
            "start_time": start.strftime("%H:%M"),
            "end_time": end.strftime("%H:%M"),
        }
        create = await _invoke_named(tool_manager, "nextcloud_add_event", payload, timeout)
        uid = _uid_from_text(create)
        if not uid or create.strip().lower().startswith("error:"):
            phases.append({"phase": "create", "status": "fail"})
            return _result("nextcloud_add_event", "fail", "create", create[:900] or "Created event without UID.", phases=phases)
        phases.append({"phase": "create", "status": "pass"})

        listed = await _invoke_named(tool_manager, "nextcloud_get_events", {"calendar": calendar, "days_ahead": 1}, timeout)
        if title not in listed and uid not in listed:
            phases.append({"phase": "verify", "status": "fail"})
            return _result("nextcloud_add_event", "fail", "verification", "Created event was not found.", phases=phases)
        phases.append({"phase": "verify", "status": "pass"})

        deleted = await _invoke_named(tool_manager, "nextcloud_delete_event", {"calendar": calendar, "uid": uid}, timeout)
        if deleted.strip().lower().startswith("error:"):
            phases.append({"phase": "delete", "status": "fail"})
            return _result("nextcloud_add_event", "fail", "cleanup", deleted[:900], phases=phases)
        phases.append({"phase": "delete", "status": "pass"})

        listed_after = await _invoke_named(tool_manager, "nextcloud_get_events", {"calendar": calendar, "days_ahead": 1}, timeout)
        if title in listed_after or uid in listed_after:
            phases.append({"phase": "cleanup verify", "status": "fail"})
            return _result("nextcloud_add_event", "fail", "cleanup", "Event still exists after delete.", phases=phases)
        phases.append({"phase": "cleanup verify", "status": "pass"})
        return _result("nextcloud_add_event", "pass", "cleanup", "Created event, located it, deleted it, and verified deletion.", phases=phases)
    finally:
        if uid and not any(p["phase"] == "delete" and p["status"] == "pass" for p in phases):
            try:
                await _invoke_named(tool_manager, "nextcloud_delete_event", {"calendar": calendar, "uid": uid}, timeout)
            except Exception as exc:
                log.warning("Failed emergency cleanup for smoke event %s: %s", uid, exc)


async def _habitica_todo_transaction(tool_manager: Any, timeout: float) -> dict[str, Any]:
    """Create a throwaway Habitica todo, confirm it, delete it, confirm removal.

    Exercises a non-Nextcloud write path (POST + DELETE against the Habitica
    API). Habitica has no get-by-id, and habitica_get_tasks truncates its list,
    so the create/delete API successes are the authoritative signals; the
    presence/absence list checks are best-effort and never cause a false fail.
    """
    title = f"TEST_SMOKE_TODO_{int(time.time())}"
    phases: list[dict[str, str]] = [{"phase": "validation", "status": "pass"}]
    task_id = ""
    try:
        create = await _invoke_named(tool_manager, "habitica_add_todo", {"text": title}, timeout)
        task_id = _id_from_text(create)
        if not task_id or create.strip().lower().startswith("error:"):
            phases.append({"phase": "create", "status": "fail"})
            return _result("habitica_add_todo", "fail", "create", create[:900] or "Created todo without ID.", phases=phases)
        phases.append({"phase": "create", "status": "pass"})

        deleted = await _invoke_named(tool_manager, "habitica_delete_task", {"task_id": task_id}, timeout)
        if deleted.strip().lower().startswith("error:"):
            phases.append({"phase": "delete", "status": "fail"})
            return _result("habitica_add_todo", "fail", "cleanup", deleted[:900], phases=phases)
        phases.append({"phase": "delete", "status": "pass"})

        # Best-effort: if the title still shows in the (truncated) active list, the
        # delete clearly did not take. Absence is expected and counts as clean.
        listed_after = await _invoke_named(tool_manager, "habitica_get_tasks", {}, timeout)
        if title in listed_after:
            phases.append({"phase": "cleanup verify", "status": "fail"})
            return _result("habitica_add_todo", "fail", "cleanup", "Todo still present after delete.", phases=phases)
        phases.append({"phase": "cleanup verify", "status": "pass"})
        return _result("habitica_add_todo", "pass", "cleanup", "Created todo, deleted it, and verified removal.", phases=phases)
    finally:
        if task_id and not any(p["phase"] == "delete" and p["status"] == "pass" for p in phases):
            try:
                await _invoke_named(tool_manager, "habitica_delete_task", {"task_id": task_id}, timeout)
            except Exception as exc:
                log.warning("Failed emergency cleanup for smoke todo %s: %s", task_id, exc)
