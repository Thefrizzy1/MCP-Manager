"""Shared HTTP dependencies: Basic-auth verification and the CSRF origin guard.

Moved verbatim from ``main.py``. ``verify_auth`` is attached at the router level
in ``build_ui_app`` so a new endpoint cannot be added unguarded by accident; the
handful of deliberately public routes opt out explicitly.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core import ui_users
from core.rate_limit import LoginRateLimiter

# auto_error=False so verify_auth's own body runs even when no Authorization
# header is present — that is the case where a browser must be allowed to fall
# back to its session cookie instead of being hit with a 401 Basic challenge.
security = HTTPBasic(auto_error=False)
_login_limiter = LoginRateLimiter()

# Repo root (ui/api/deps.py -> ../../..). Computed locally rather than imported
# from ui.runtime to keep this module cheap and free of that heavy import.
ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_COOKIE = "plutus_session"

_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _client_key(request: Request) -> str:
    return request.client.host if request and request.client else "unknown"


def _principal(username: str, via: str) -> dict:
    rec = ui_users.get_user(ROOT, username)
    role = rec["role"] if rec else "admin"  # env-fallback admin has no store row
    return {"username": username, "role": role, "via": via}


def verify_auth(request: Request, creds: HTTPBasicCredentials | None = Depends(security)):
    """Authenticate a dashboard request by session cookie **or** HTTP Basic.

    Browsers use the signed session cookie minted by /api/v1/auth/login; API
    clients (curl, n8n) keep using Basic auth. A browser that is not signed in
    gets a plain 401 (no ``WWW-Authenticate: Basic``, so no native dialog) and the
    frontend redirects it to /login; a non-browser client still gets the Basic
    challenge so it knows to send credentials.
    """
    # 1. Session cookie (the browser path).
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        user = ui_users.verify_session(ROOT, token)
        if user:
            return _principal(user, "session")

    # 2. No credential is even possible -> configuration error, not a 401.
    if not ui_users.auth_available(ROOT):
        raise HTTPException(
            503,
            "No UI credential is configured. On first run Plutus seeds admin/adminadmin; "
            "if you cleared it, set UI_PASSWORD in .env or set "
            "PLUTUS_ALLOW_EMPTY_UI_PASSWORD=1 for isolated local development only.",
        )

    key = _client_key(request)
    remaining = _login_limiter.locked_for(key, time.time())
    if remaining > 0:
        raise HTTPException(
            429,
            f"Too many failed logins. Try again in {int(remaining) + 1}s.",
            headers={"Retry-After": str(int(remaining) + 1)},
        )

    # 3. HTTP Basic (the API-client path).
    if creds is not None:
        rec = ui_users.verify_credentials(ROOT, creds.username, creds.password)
        if rec is not None:
            _login_limiter.record_success(key)
            return _principal(creds.username, "basic")
        locked = _login_limiter.record_failure(key, time.time())
        if locked > 0:
            raise HTTPException(
                429,
                f"Too many failed logins. Locked for {int(locked)}s.",
                headers={"Retry-After": str(int(locked))},
            )
        raise HTTPException(401, "Bad credentials", headers={"WWW-Authenticate": "Basic"})

    # 4. Unauthenticated and no credential offered. Don't provoke the browser's
    #    Basic dialog; the frontend redirects HTML requests to /login. Non-browser
    #    clients get the challenge so they know Basic auth is expected.
    accepts_html = "text/html" in (request.headers.get("accept") or "")
    headers = {} if accepts_html else {"WWW-Authenticate": "Basic"}
    raise HTTPException(401, "Authentication required. Sign in at /login.", headers=headers)


def require_admin(principal: dict = Depends(verify_auth)) -> dict:
    """Gate a route to admins only. Non-admins get 403."""
    if principal.get("role") != "admin":
        raise HTTPException(403, "Admin access required.")
    return principal


async def csrf_origin_guard(request: Request, call_next):
    """Reject cross-site state-changing requests. A browser auto-sends `Origin`
    on cross-site POSTs; the dashboard's own fetch() is same-origin so it
    matches. Non-browser clients (curl, n8n) send no Origin and pass through —
    they authenticate with Basic auth and aren't subject to CSRF. Set
    PLUTUS_DISABLE_CSRF=1 to disable (e.g. an unusual proxy setup)."""
    if request.method not in _CSRF_SAFE_METHODS and os.getenv("PLUTUS_DISABLE_CSRF", "").strip().lower() not in ("1", "true", "yes"):
        origin = request.headers.get("origin")
        if origin:
            # Compare host *and* port. Hostname alone treats every other service on
            # the same box (a homelab runs a dozen) as same-origin, so any of them
            # could forge state-changing requests at the dashboard.
            parsed = urlparse(origin)
            origin_host = (parsed.hostname or "").lower()
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)

            def _hostport(raw: str) -> tuple[str, int]:
                h = raw.split(",")[0].strip().lower()
                if h.startswith("[") and "]" in h:            # bracketed IPv6
                    host, _, rest = h.partition("]")
                    return host[1:], int(rest.lstrip(":") or 0) or origin_port
                host, _, port = h.partition(":")
                return host, (int(port) if port.isdigit() else origin_port)

            allowed = {
                _hostport(h)
                for h in (request.headers.get("host"), request.headers.get("x-forwarded-host"))
                if h
            }
            if origin_host and allowed and (origin_host, origin_port) not in allowed:
                return JSONResponse(
                    {"detail": "Cross-origin request rejected (CSRF protection). Set PLUTUS_DISABLE_CSRF=1 if this is a false positive."},
                    status_code=403,
                )
    return await call_next(request)
