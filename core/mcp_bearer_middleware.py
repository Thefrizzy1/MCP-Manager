"""Optional Bearer gate for MCP streamable-http (Authorization: Bearer <token>).

The MCP server runs in a different process from the Web UI, so each has its own
boot-time ``cfg`` snapshot. If this gate read ``cfg.mcp_require_bearer`` /
``cfg.mcp_bearer_token`` directly, enabling auth or rotating the token in the UI
would have no effect on the live gate until a full restart — a security toggle
that silently does nothing.

Instead the gate reads the flag and token from ``.env`` at request time (via
core.env_store), behind a small TTL cache so it costs at most one tiny file read
every few seconds. A change made in the UI takes effect within the TTL.
"""

from __future__ import annotations

import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.env_store import read_env

# Cache the parsed auth config briefly so we don't read .env on every request.
_TTL_SECONDS = 3.0
_cache: dict | None = None
_cache_ts: float = 0.0


def _auth_config() -> tuple[bool, str]:
    """(require_bearer, token) read from .env, cached for _TTL_SECONDS."""
    global _cache, _cache_ts
    now = time.time()
    if _cache is None or (now - _cache_ts) > _TTL_SECONDS:
        env = read_env()
        require = str(env.get("MCP_REQUIRE_BEARER", "")).strip().lower() in ("true", "1", "yes")
        token = (env.get("MCP_BEARER_TOKEN", "") or "").strip()
        _cache = {"require": require, "token": token}
        _cache_ts = now
    return _cache["require"], _cache["token"]


class MCPBearerGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        require_bearer, expected = _auth_config()
        if not require_bearer:
            return await call_next(request)
        if not expected:
            return JSONResponse(
                {"detail": "MCP auth required but MCP_BEARER_TOKEN is not set. Generate a token in the UI Settings."},
                status_code=503,
            )
        auth = (request.headers.get("authorization") or "").strip()
        if not auth.lower().startswith("bearer "):
            return JSONResponse(
                {"detail": "Bearer token required", "hint": "Send Authorization: Bearer <your MCP token>"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="mcp"'},
            )
        got = auth[7:].strip()
        if not secrets.compare_digest(got, expected):
            return JSONResponse({"detail": "Invalid bearer token"}, status_code=401)
        return await call_next(request)
