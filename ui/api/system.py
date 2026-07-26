"""System surface: dashboard, capabilities, router, observability, MCP selftest,
direct tool run, and the SPA shell. All authed."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from config import cfg
from core.dashboard_api import build_dashboard_payload
from core.invoke_tool import invoke_mcp_tool_fn
from core.recent_runs import append_recent, ensure_data_dir, load_recent
from core.result_status import text_looks_successful
from core.tool_cache import record_tool_output
from ui.api.deps import verify_auth
from ui.runtime import (
    ROOT,
    capabilities,
    get_health,
    load_env,
    log,
    router_runtime,
    telemetry,
    tools,
)
from ui.spa_page import render_spa

router = APIRouter(dependencies=[Depends(verify_auth)])


@router.get("/app", response_class=HTMLResponse)
async def spa():
    return HTMLResponse(render_spa(), headers={"Cache-Control": "no-store"})


@router.get("/agents")
async def agents_page():
    # The agent workspace now lives inside the SPA.
    return RedirectResponse(url="/app#/agents")


@router.get("/api/v1/dashboard")
async def api_v1_dashboard(request: Request):
    """Structured dashboard JSON. Query: ?sections=networking,main,tools,services,auth,recent (comma-sep). Omit for all."""
    raw = (request.query_params.get("sections") or "").strip()
    sections = {s.strip().lower() for s in raw.split(",") if s.strip()}
    health = await get_health()
    recent = load_recent(ROOT)
    tool_names = capabilities.tool_names()
    payload = build_dashboard_payload(
        health_cache=health,
        tool_names=tool_names,
        recent=list(recent or []),
        sections=sections,
        local_ip_hint=cfg.mcp_lan_host,
    )
    return payload


@router.get("/api/v1/capabilities")
async def api_v1_capabilities(request: Request):
    include_tools = (request.query_params.get("include_tools") or "").strip().lower() in {"1", "true", "yes"}
    return capabilities.payload(include_tools=include_tools)


class RouteBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@router.post("/api/v1/router/route")
async def api_v1_router_route(body: RouteBody):
    return await router_runtime.handle(body.text)


@router.get("/api/v1/observability")
async def api_v1_observability():
    return telemetry.snapshot()


@router.get("/api/v1/mcp/selftest")
async def api_v1_mcp_selftest():
    """Probe the live MCP endpoint the way a client would, so the Connection
    Manager can show green/red before you paste a config. Sends an `initialize`
    request to 127.0.0.1:<mcp_port>/mcp with the Bearer token when required.
    """
    import httpx

    url = f"http://127.0.0.1:{cfg.mcp_port}/mcp"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if cfg.mcp_require_bearer:
        tok = (load_env().get("MCP_BEARER_TOKEN", "") or "").strip()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "plutus-selftest", "version": "1"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(url, headers=headers, json=body)
        status = r.status_code
        if status in (401, 403):
            return {"ok": False, "reachable": True, "status": status,
                    "detail": "Reachable, but the Bearer token was rejected or missing. Regenerate/enable it under MCP Bearer Auth."}
        if status == 503:
            return {"ok": False, "reachable": True, "status": status,
                    "detail": "Reachable, but MCP auth is required and no token is set. Generate one under MCP Bearer Auth."}
        if 200 <= status < 500:
            return {"ok": True, "reachable": True, "status": status,
                    "detail": f"MCP endpoint responded (HTTP {status}). Clients should be able to connect."}
        return {"ok": False, "reachable": True, "status": status,
                "detail": f"MCP endpoint returned HTTP {status}."}
    except Exception as e:
        return {"ok": False, "reachable": False, "status": 0,
                "detail": f"Could not reach the MCP endpoint on port {cfg.mcp_port}: {type(e).__name__}. Is the MCP process running?"}


class ToolRunBody(BaseModel):
    tool: str
    params: dict = {}


@router.post("/tool/run")
async def tool_run(body: ToolRunBody):
    try:
        started = time.perf_counter()
        tool = tools.get_tool(body.tool)
        if not tool:
            return JSONResponse({"error": f"Tool '{body.tool}' not available (disabled or not registered)"})
        result = await asyncio.wait_for(invoke_mcp_tool_fn(tool.fn, payload=body.params), timeout=30.0)
        ensure_data_dir(ROOT); append_recent(ROOT, {"tool": body.tool, "ts": time.strftime("%H:%M")})
        text = json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
        ok = text_looks_successful(text)
        telemetry.record(
            route="tool.direct",
            status="ok" if ok else "fail",
            latency_ms=int((time.perf_counter() - started) * 1000),
            detail=body.tool,
        )
        try:
            record_tool_output(ROOT, body.tool, text, ok=ok)
        except Exception as exc:
            log.warning("Failed to record tool output for %s: %s", body.tool, exc)
        if isinstance(result, (dict, list)):
            return JSONResponse({"result": result})
        return JSONResponse({"result": text})
    except asyncio.TimeoutError:
        return JSONResponse({"error": f"Timeout (30s) running {body.tool}"})
    except Exception as e:
        return JSONResponse({"error": str(e)})
