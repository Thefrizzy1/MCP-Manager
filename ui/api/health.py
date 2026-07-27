"""Service/tool health surface: refresh, full report, regression check."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from config import cfg
from core.batch_health import run_health_batch_for_ui
from core.dashboard_health import build_health_report_markdown, gather_service_health
from core.invoke_tool import invoke_mcp_tool_fn
from ui.api.deps import verify_auth
from ui import runtime
from ui.runtime import ROOT, _services_live, tools

router = APIRouter(dependencies=[Depends(verify_auth)])


@router.get("/health/refresh")
async def health_refresh():
    async with runtime._health_lock:
        cache, rows = await gather_service_health(_services_live(), cfg)
        runtime._health_cache = cache
        runtime._health_states = {r["id"]: r.get("state") for r in rows}
        runtime._health_ts = time.time()
    return cache


@router.post("/health/full-report")
async def health_full_report():
    """Refresh service probes, run zero-arg tool batch, return markdown + structured rows."""
    async with runtime._health_lock:
        cache, svc_rows = await gather_service_health(_services_live(), cfg)
        runtime._health_cache = cache
        runtime._health_states = {r["id"]: r.get("state") for r in svc_rows}
        runtime._health_ts = time.time()
    tool_rows = await run_health_batch_for_ui(tools.raw_manager)
    md = build_health_report_markdown(svc_rows, tool_rows)
    return {"health": cache, "services": svc_rows, "tools": tool_rows, "markdown": md}


@router.post("/api/v1/health/regression-check")
async def api_v1_health_regression_check(request: Request):
    """Run the tool health batch, diff against the saved baseline, and report
    regressions (tools that worked before and fail now). ?notify=1 pushes an
    ntfy alert when regressions exist. ?dry=1 skips updating the baseline.

    Intended to be hit on a schedule (n8n cron / scheduled-tasks / cron curl).
    """
    from core.health_regression import run_regression_check

    notify_on = (request.query_params.get("notify") or "").strip().lower() in {"1", "true", "yes"}
    dry = (request.query_params.get("dry") or "").strip().lower() in {"1", "true", "yes"}

    notify_fn = None
    if notify_on:
        ntfy_tool = tools.get_tool("ntfy_send")
        if ntfy_tool is not None:
            async def _notify(message: str):
                await invoke_mcp_tool_fn(
                    ntfy_tool.fn,
                    payload={"message": message, "title": "Plutus health regression", "priority": "high"},
                )
            notify_fn = _notify

    return await run_regression_check(
        ROOT, tools.raw_manager, notify=notify_fn, update_baseline=not dry
    )
