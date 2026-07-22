"""Deterministic request router for low-token MCP dispatch."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from core.invoke_tool import invoke_mcp_tool_fn
from core.result_status import text_looks_successful

_STATUS_WORDS = frozenset({"status", "health"})
_HOMELAB_WORDS = frozenset({"server", "service", "services", "homelab", "router"})
_TASK_WORDS = frozenset({"task", "tasks"})
_LIST_WORDS = frozenset({"list", "show", "get"})
_WORD_TOOL_ROUTES = (
    ({"nextcloud"}, {"calendar", "calendars"}, None, "nextcloud_list_calendars", {}),
    ({"nextcloud"}, _TASK_WORDS, _LIST_WORDS, "nextcloud_get_tasks", {"list_name": "tasks"}),
    ({"weather"}, None, None, "weather_current", {}),
    ({"docker"}, {"container", "containers"}, None, "docker_list_containers", {}),
)


@dataclass(frozen=True)
class Route:
    level: int
    route: str
    tool: str | None = None
    params: dict[str, Any] | None = None


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def compact_context_snapshot(*, active_capabilities: list[str] | None = None) -> dict[str, Any]:
    return {
        "mode": "mcp_router",
        "active_capabilities": sorted(active_capabilities or []),
    }


def route_intent(text: str) -> Route:
    q = " ".join((text or "").strip().lower().split())
    if not q:
        return Route(level=0, route="empty")
    words = frozenset(q.split())

    if q in _STATUS_WORDS or (words & _HOMELAB_WORDS and words & _STATUS_WORDS):
        return Route(level=1, route="skill.homelab_status")
    for required, any_words, extra_words, tool, params in _WORD_TOOL_ROUTES:
        if required <= words and (any_words is None or words & any_words) and (extra_words is None or words & extra_words):
            return Route(level=0, route="tool", tool=tool, params=params)

    return Route(level=2, route="llm_fallback")


async def execute_route(
    *,
    route: Route,
    tool_manager: Any,
    health_fn,
    timeout: float = 30.0,
) -> dict[str, Any]:
    started = time.perf_counter()
    if route.route == "empty":
        return _response("warning", route, started, data={}, error="Empty request.")
    if route.route == "llm_fallback":
        return _response(
            "warning",
            route,
            started,
            data={"fallback": "llm", "context": compact_context_snapshot()},
            error="No deterministic route matched.",
        )
    if route.route == "skill.homelab_status":
        health = await health_fn()
        up = sorted(k for k, v in health.items() if v is True)
        down = sorted(k for k, v in health.items() if v is False)
        unknown = sorted(k for k, v in health.items() if v is None)
        return _response(
            "ok",
            route,
            started,
            data={"up": up, "down": down, "unknown": unknown, "counts": {"up": len(up), "down": len(down), "unknown": len(unknown)}},
        )
    if route.route == "tool" and route.tool:
        tool = tool_manager.get_tool(route.tool)
        if not tool:
            return _response("fail", route, started, data={}, error=f"Tool not available: {route.tool}")
        output = await asyncio.wait_for(invoke_mcp_tool_fn(tool.fn, payload=route.params or {}), timeout=timeout)
        status = "ok" if not isinstance(output, str) or text_looks_successful(output) else "fail"
        return _response(status, route, started, data={"tool": route.tool, "result": output})
    return _response("fail", route, started, data={}, error=f"Unsupported route: {route.route}")


def _response(status: str, route: Route, started: float, *, data: dict[str, Any], error: str | None = None) -> dict[str, Any]:
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "status": status,
        "route": route.route,
        "level": route.level,
        "tool": route.tool,
        "data": data,
        "error": error,
        "meta": {"latency_ms": latency_ms, "tokens_est": 0 if route.level <= 1 else 0},
    }


class RouterRuntime:
    """Route, execute, and record one deterministic router request."""

    def __init__(self, *, tool_manager: Any, health_fn: Callable, telemetry: Any):
        self.tool_manager = tool_manager
        self.health_fn = health_fn
        self.telemetry = telemetry

    async def handle(self, text: str) -> dict[str, Any]:
        route = route_intent(text)
        result = await execute_route(route=route, tool_manager=self.tool_manager, health_fn=self.health_fn)
        meta = result.setdefault("meta", {})
        tokens_est = estimate_tokens(text) if route.level >= 2 else 0
        meta["tokens_est"] = tokens_est
        self.telemetry.record(
            route=str(result.get("route") or "unknown"),
            status=str(result.get("status") or "unknown"),
            latency_ms=int(meta.get("latency_ms") or 0),
            tokens_est=tokens_est,
            detail=str(result.get("error") or result.get("tool") or ""),
        )
        return result
