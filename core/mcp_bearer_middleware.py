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

from core.env_store import ENV_PATH, read_env
from core.oauth_routes import external_base, is_oauth_path

ROOT = ENV_PATH.parent

# Cache the parsed auth config briefly so we don't read .env on every request.
_TTL_SECONDS = 3.0
_cache: dict | None = None
_cache_ts: float = 0.0


def _auth_config() -> tuple[bool, str, bool]:
    """(require_bearer, static_token, oauth_enabled) read from .env, cached."""
    global _cache, _cache_ts
    now = time.time()
    if _cache is None or (now - _cache_ts) > _TTL_SECONDS:
        env = read_env()
        require = str(env.get("MCP_REQUIRE_BEARER", "")).strip().lower() in ("true", "1", "yes")
        token = (env.get("MCP_BEARER_TOKEN", "") or "").strip()
        oauth = str(env.get("MCP_OAUTH_ENABLED", "")).strip().lower() in ("true", "1", "yes")
        _cache = {"require": require, "token": token, "oauth": oauth}
        _cache_ts = now
    return _cache["require"], _cache["token"], _cache["oauth"]


def _token_ok(got: str, expected: str, oauth: bool) -> bool:
    if expected and secrets.compare_digest(got, expected):
        return True
    if oauth:
        from core import oauth_provider
        return oauth_provider.validate_access_token(ROOT, got)
    return False


class MCPBearerGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # OAuth discovery/login/token endpoints are public by design.
        if is_oauth_path(request.url.path):
            return await call_next(request)

        require_bearer, expected, oauth = _auth_config()
        if not require_bearer:
            return await call_next(request)
        if not expected and not oauth:
            return JSONResponse(
                {"detail": "MCP auth required but neither MCP_BEARER_TOKEN nor MCP_OAUTH_ENABLED is set. "
                           "Generate a token or enable OAuth in the UI Settings."},
                status_code=503,
            )

        auth = (request.headers.get("authorization") or "").strip()
        if not auth.lower().startswith("bearer "):
            # Advertise the OAuth resource metadata so browser connectors can start
            # the sign-in flow; keep the plain realm challenge for token-only mode.
            if oauth:
                challenge = f'Bearer resource_metadata="{external_base(request)}/.well-known/oauth-protected-resource"'
            else:
                challenge = 'Bearer realm="mcp"'
            return JSONResponse(
                {"detail": "Authorization required", "hint": "Send Authorization: Bearer <token>"},
                status_code=401,
                headers={"WWW-Authenticate": challenge},
            )
        if not _token_ok(auth[7:].strip(), expected, oauth):
            return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
        return await call_next(request)
