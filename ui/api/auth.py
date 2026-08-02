"""Authentication surface: the login page, session cookie lifecycle, and the
admin user-management API.

Two routers:
  - ``public_router`` — no auth: the /login page, the login/logout endpoints, and
    the /app shell (which redirects to /login when there is no valid session).
  - ``router`` — behind ``verify_auth``: whoami, self password change, and the
    admin-only user CRUD.

Sessions are stateless signed cookies (see core/ui_users). Login is rate-limited
by the same limiter that guards Basic auth.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from core import ui_users
from ui.api.deps import (
    ROOT,
    SESSION_COOKIE,
    _client_key,
    _login_limiter,
    require_admin,
    verify_auth,
)
from ui.login_page import render_login_page

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(verify_auth)])


def _authed_username(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    return ui_users.verify_session(ROOT, token) if token else None


def _set_session_cookie(resp, request: Request, username: str, remember: bool) -> None:
    token = ui_users.sign_session(ROOT, username, remember=remember)
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=ui_users.REMEMBER_TTL if remember else None,  # None -> session cookie
        httponly=True, samesite="lax",
        secure=request.url.scheme == "https", path="/",
    )


# ── public: the login page + session lifecycle ───────────────────────────────

@public_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _authed_username(request):
        return RedirectResponse(url="/app", status_code=303)
    return HTMLResponse(
        render_login_page(default_active=ui_users.default_password_active(ROOT)),
        headers={"Cache-Control": "no-store"},
    )


@public_router.get("/app", response_class=HTMLResponse)
async def app_shell(request: Request):
    """Serve the SPA shell only to a signed-in browser; otherwise send it to
    /login. The shell carries no secrets (data lives behind authed APIs), but
    bouncing here avoids a flash of the app before the first 401."""
    if not _authed_username(request):
        return RedirectResponse(url="/login", status_code=303)
    from ui.runtime import DIST_DIR
    index = DIST_DIR / "index.html"
    if index.is_file():
        return HTMLResponse(index.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})
    from ui.spa_page import render_spa
    return HTMLResponse(render_spa(), headers={"Cache-Control": "no-store"})


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=512)
    remember: bool = False


@public_router.post("/api/v1/auth/login")
async def login(request: Request, body: LoginBody):
    key = _client_key(request)
    remaining = _login_limiter.locked_for(key, time.time())
    if remaining > 0:
        raise HTTPException(
            429, f"Too many failed logins. Try again in {int(remaining) + 1}s.",
            headers={"Retry-After": str(int(remaining) + 1)},
        )
    rec = ui_users.verify_credentials(ROOT, body.username, body.password)
    if rec is None:
        locked = _login_limiter.record_failure(key, time.time())
        if locked > 0:
            raise HTTPException(
                429, f"Too many failed logins. Locked for {int(locked)}s.",
                headers={"Retry-After": str(int(locked))},
            )
        raise HTTPException(401, "Incorrect username or password.")
    _login_limiter.record_success(key)
    resp = JSONResponse({
        "ok": True,
        "username": body.username,
        "role": rec.get("role", "user"),
        "must_change": bool(rec.get("must_change")),
    })
    _set_session_cookie(resp, request, body.username, body.remember)
    return resp


@public_router.post("/api/v1/auth/logout")
async def logout(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ── authed: identity + self-service ──────────────────────────────────────────

@router.get("/api/v1/auth/whoami")
async def whoami(principal: dict = Depends(verify_auth)):
    rec = ui_users.get_user(ROOT, principal["username"]) or {}
    return {
        "username": principal["username"],
        "role": principal.get("role", "user"),
        "must_change": bool(rec.get("must_change")),
        "default_password_active": ui_users.default_password_active(ROOT),
    }


class ChangePasswordBody(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=512)
    new_password: str = Field(..., min_length=8, max_length=512)


@router.post("/api/v1/auth/change-password")
async def change_password(body: ChangePasswordBody, principal: dict = Depends(verify_auth)):
    username = principal["username"]
    if ui_users.verify_credentials(ROOT, username, body.current_password) is None:
        raise HTTPException(400, "Your current password is incorrect.")
    try:
        ui_users.set_password(ROOT, username, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ── admin: user management ───────────────────────────────────────────────────

@router.get("/api/v1/auth/users")
async def list_users(_: dict = Depends(require_admin)):
    return {"users": ui_users.list_users(ROOT)}


class AddUserBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=8, max_length=512)
    role: str = Field(default="user")


@router.post("/api/v1/auth/users")
async def add_user(body: AddUserBody, _: dict = Depends(require_admin)):
    try:
        created = ui_users.add_user(ROOT, body.username, body.password, role=body.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **created}


@router.delete("/api/v1/auth/users/{username}")
async def remove_user(username: str, principal: dict = Depends(require_admin)):
    if username == principal["username"]:
        raise HTTPException(400, "You cannot remove your own account.")
    try:
        ui_users.remove_user(ROOT, username)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class SetRoleBody(BaseModel):
    role: str = Field(...)


@router.post("/api/v1/auth/users/{username}/role")
async def set_role(username: str, body: SetRoleBody, _: dict = Depends(require_admin)):
    try:
        ui_users.set_role(ROOT, username, body.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class SetPasswordBody(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=512)


@router.post("/api/v1/auth/users/{username}/password")
async def reset_password(username: str, body: SetPasswordBody, _: dict = Depends(require_admin)):
    try:
        ui_users.set_password(ROOT, username, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
