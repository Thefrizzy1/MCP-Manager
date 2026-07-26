"""Shared HTTP dependencies: Basic-auth verification and the CSRF origin guard.

Moved verbatim from ``main.py``. ``verify_auth`` is attached at the router level
in ``build_ui_app`` so a new endpoint cannot be added unguarded by accident; the
handful of deliberately public routes opt out explicitly.
"""
from __future__ import annotations

import os
import secrets
import time
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config import allow_empty_ui_password, cfg
from core.rate_limit import LoginRateLimiter

security = HTTPBasic()
_login_limiter = LoginRateLimiter()

_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _client_key(request: Request) -> str:
    return request.client.host if request and request.client else "unknown"


def verify_auth(request: Request, creds: HTTPBasicCredentials = Depends(security)):
    if not (cfg.ui_password or allow_empty_ui_password()):
        raise HTTPException(
            503,
            "UI_PASSWORD is not set. Add it to .env (see .env.example), or set "
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
    user_ok = secrets.compare_digest(creds.username, cfg.ui_username)
    if cfg.ui_password:
        pass_ok = secrets.compare_digest(creds.password, cfg.ui_password)
    else:
        pass_ok = allow_empty_ui_password() and not creds.password
    if not (user_ok and pass_ok):
        locked = _login_limiter.record_failure(key, time.time())
        if locked > 0:
            raise HTTPException(
                429,
                f"Too many failed logins. Locked for {int(locked)}s.",
                headers={"Retry-After": str(int(locked))},
            )
        raise HTTPException(401, "Bad credentials", headers={"WWW-Authenticate": "Basic"})
    _login_limiter.record_success(key)
    return creds


async def csrf_origin_guard(request: Request, call_next):
    """Reject cross-site state-changing requests. A browser auto-sends `Origin`
    on cross-site POSTs; the dashboard's own fetch() is same-origin so it
    matches. Non-browser clients (curl, n8n) send no Origin and pass through —
    they authenticate with Basic auth and aren't subject to CSRF. Set
    PLUTUS_DISABLE_CSRF=1 to disable (e.g. an unusual proxy setup)."""
    if request.method not in _CSRF_SAFE_METHODS and os.getenv("PLUTUS_DISABLE_CSRF", "").strip().lower() not in ("1", "true", "yes"):
        origin = request.headers.get("origin")
        if origin:
            origin_host = (urlparse(origin).hostname or "").lower()
            allowed = {
                h.split(",")[0].split(":")[0].strip().lower()
                for h in (request.headers.get("host"), request.headers.get("x-forwarded-host"))
                if h
            }
            if origin_host and allowed and origin_host not in allowed:
                return JSONResponse(
                    {"detail": "Cross-origin request rejected (CSRF protection). Set PLUTUS_DISABLE_CSRF=1 if this is a false positive."},
                    status_code=403,
                )
    return await call_next(request)
