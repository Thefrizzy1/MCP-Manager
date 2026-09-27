"""Connect a Google account for YouTube Analytics, impressions/CTR and Search Console.

Tokens never travel back out — every response is ``google_oauth.status``, which
names the connected channel and nothing it is authenticated with.

Starting, finishing and disconnecting are admin-only: the login is the server's,
shared by every agent and every dashboard user, not a per-user preference.

``/api/v1/google/callback`` is the one public route here. Google's redirect lands
in whichever browser did the consenting, and often on ``localhost`` — a
different origin from the dashboard, so no session cookie comes with it. What
makes it safe is the OAuth ``state``: it must match a sign-in this process
started in the last 15 minutes (32 random bytes, held only in memory), and the
PKCE verifier that the code exchange needs never leaves the server.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from core import google_oauth as go
from ui.api.deps import require_admin, verify_auth
from ui.runtime import ROOT

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(verify_auth)])


async def _setup_reach_job() -> None:
    """Register the daily impressions report while consent is fresh. Best effort:
    the connection is good whether or not this works, and the error is kept so
    Settings can say why instead of the tool failing mysteriously later."""
    from core import youtube_reporting as yr
    try:
        token = await go.access_token(ROOT)
        job, created = await yr.ensure_reach_job(token)
        go.update_meta(ROOT, reach_job_created=str(job.get("createTime") or "")[:10] or "yes",
                       reach_job_error="")
    except Exception as e:
        go.update_meta(ROOT, reach_job_error=str(e)[:300])


@router.get("/api/v1/google/status")
async def api_google_status():
    return go.status(ROOT)


@router.post("/api/v1/google/start")
async def api_google_start(_: dict = Depends(require_admin)):
    try:
        return go.start()
    except go.NotConnected as e:
        raise HTTPException(400, str(e))


class FinishBody(BaseModel):
    response: str = Field(..., min_length=4, max_length=4000)


@router.post("/api/v1/google/finish")
async def api_google_finish(body: FinishBody, _: dict = Depends(require_admin)):
    try:
        await go.finish(ROOT, body.response)
    except (ValueError, go.NotConnected) as e:
        raise HTTPException(400, str(e))
    await _setup_reach_job()
    return go.status(ROOT)


@router.post("/api/v1/google/reach-job")
async def api_google_reach_job(_: dict = Depends(require_admin)):
    if not go.status(ROOT)["connected"]:
        raise HTTPException(400, "connect a Google account first")
    await _setup_reach_job()
    return go.status(ROOT)


@router.post("/api/v1/google/disconnect")
async def api_google_disconnect(_: dict = Depends(require_admin)):
    return await go.disconnect(ROOT)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
        "<style>body{font:15px system-ui,sans-serif;max-width:34rem;margin:15vh auto;padding:0 1rem;"
        "color:#222;background:#fafafa}@media(prefers-color-scheme:dark){body{color:#ddd;background:#141414}}"
        "a{color:inherit}</style>"
        f"<h1 style='font-size:1.2rem'>{html.escape(title)}</h1><p>{body}</p>",
        status_code=status, headers={"Cache-Control": "no-store"})


@public_router.get(go.CALLBACK_PATH, response_class=HTMLResponse)
async def google_callback(request: Request):
    q = request.query_params
    if not q.get("state"):
        return _page("Google sign-in", "This address only finishes a sign-in started from Plutus.", 400)
    try:
        await go.finish(ROOT, str(request.url))
    except (ValueError, go.NotConnected) as e:
        return _page("Google sign-in failed", html.escape(str(e)), 400)
    await _setup_reach_job()
    st = go.status(ROOT)
    who = html.escape(st["channel_title"] or "your Google account")
    return _page("Connected", f"Plutus can now read the analytics of <b>{who}</b>. "
                              "You can close this tab.")
