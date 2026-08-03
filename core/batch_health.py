"""Shared batch invocation for MCP tool health (UI tester + MCP test_all_tools)."""

from __future__ import annotations

import asyncio
from typing import Any  # noqa: F401 used in annotations

from core.invoke_tool import invoke_mcp_tool_fn
from core.result_status import text_looks_successful
from core.tool_registry import (
    ZERO_PARAM_HEALTH_TOOLS,
    is_tool_environment_ready,
    looks_like_missing_service_config,
    merged_tool_payload,
)



async def run_health_batch_for_ui(
    tool_manager: Any, *, timeout: float = 120.0, concurrency: int = 8
) -> list[dict]:
    """Probe every zero-param health tool and classify each pass/unset/fail/skip.

    Runs the probes concurrently (bounded by ``concurrency``) instead of serially:
    one hung integration used to stall the whole batch for up to ``timeout``
    seconds *each*, so the caller's overall deadline fired mid-batch and a single
    slow service failed the entire health refresh. Now a slow tool only occupies
    one slot while the rest complete. Output order matches ZERO_PARAM_HEALTH_TOOLS
    (``asyncio.gather`` preserves input order).
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _probe_one(tool_name: str) -> dict:
        row: dict = {"name": tool_name, "kind": "", "detail": "", "snippet": ""}
        tool = tool_manager.get_tool(tool_name)
        if not tool:
            row.update({"kind": "skip", "detail": "not registered or disabled"})
            return row
        if not is_tool_environment_ready(tool_name):
            row.update({"kind": "unset", "detail": "Not set up (credentials or host tool missing)"})
            return row
        try:
            merged = merged_tool_payload(tool_name, {})
            async with sem:
                output = await asyncio.wait_for(
                    invoke_mcp_tool_fn(tool.fn, payload=merged), timeout=timeout
                )
            text = "" if output is None else str(output)
            ok = text_looks_successful(text)
            snippet = text[:320] + ("…" if len(text) > 320 else "")
            if ok:
                row.update({"kind": "pass", "detail": "OK", "snippet": snippet})
            elif looks_like_missing_service_config(text):
                row.update({"kind": "unset", "detail": "Not set up / unavailable", "snippet": snippet})
            else:
                row.update({"kind": "fail", "detail": text[:200] if text else "empty response", "snippet": snippet})
        except asyncio.TimeoutError:
            row.update({"kind": "fail", "detail": f"timeout after {int(timeout)}s"})
        except Exception as e:
            row.update({"kind": "fail", "detail": str(e)[:200]})
        return row

    return list(await asyncio.gather(*(_probe_one(n) for n in ZERO_PARAM_HEALTH_TOOLS)))
