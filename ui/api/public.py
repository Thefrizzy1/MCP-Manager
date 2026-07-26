"""Deliberately unauthenticated routes: root/ui redirects and the liveness probe.

These are the only routes without ``verify_auth``. The route-guard test keeps an
explicit allowlist of exactly these paths so nothing else can go public silently.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from config import cfg
from ui.runtime import _mcp_port_alive, _started_at, _tool_count

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse(url="/app")


@router.get("/ui", response_class=HTMLResponse)
async def ui():
    # The classic dashboard was retired; the SPA at /app is the only UI now.
    return RedirectResponse(url="/app")


@router.get("/server/health")
async def server_health():
    """Unauthenticated liveness probe (used by the Docker healthcheck).

    Returns 503 if the MCP server (the actual product, in a separate process)
    is unreachable — so 'container healthy' means both halves are up, not just
    the UI answering this request.
    """
    from core.version_info import VERSION
    mcp_alive = await asyncio.to_thread(_mcp_port_alive)
    body = {
        "status": "ok" if mcp_alive else "degraded",
        "version": VERSION,
        "tools": _tool_count(),
        "mcp_port": cfg.mcp_port,
        "mcp_alive": mcp_alive,
        "ui_port": cfg.ui_port,
        "uptime_s": int(time.time() - _started_at),
    }
    return JSONResponse(body, status_code=200 if mcp_alive else 503)
