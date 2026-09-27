"""One Google login for the numbers only the channel owner can see.

An API key reads public data. Watch time, retention, the search terms viewers
typed before they found you, impressions and click-through, and Search Console
all belong to *your* account, so they need OAuth: you sign in once, Google hands
back a refresh token, and every tool call trades it for a short-lived access
token.

**Why the paste step exists.** Google only redirects back to ``localhost`` or an
``https`` address — never to ``http://192.168.x.x`` where a homelab dashboard
usually lives. So the default redirect is ``http://localhost:<UI_PORT>/…``:
opened on the server itself it lands on Plutus and finishes by itself; opened
anywhere else the browser shows "can't connect" with the code sitting in the
address bar, and you paste that address back into Settings. Using a *Desktop
app* OAuth client means no redirect URI has to be registered at all. Anyone
with an https name for Plutus sets ``GOOGLE_OAUTH_REDIRECT_URI`` and skips the
paste.

PKCE rides along on every sign-in, so a code lifted from a browser history is
useless without the verifier that only this process holds.

The refresh token lives in ``data/google_oauth.json`` (0600) — the same threat
model ``.env`` already has.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
CALLBACK_PATH = "/api/v1/google/callback"
_FILE = "google_oauth.json"
_PENDING_TTL = 15 * 60

# state -> {"verifier", "redirect_uri", "created"}. In memory on purpose: a
# sign-in that outlives a restart should be started again, not resumed.
_PENDING: dict[str, dict] = {}
# refresh_token -> (access_token, expires_at)
_ACCESS: dict[str, tuple[str, float]] = {}


class NotConnected(RuntimeError):
    """No usable Google login — the message says what to do about it."""


# ── configuration ────────────────────────────────────────────────────────────

def client_configured() -> bool:
    from config import cfg
    return bool(cfg.google_oauth_client_id and cfg.google_oauth_client_secret)


def redirect_uri() -> str:
    from config import cfg
    return (cfg.google_oauth_redirect_uri or "").strip() or f"http://localhost:{cfg.ui_port}{CALLBACK_PATH}"


NOT_CONFIGURED = (
    "Google login not configured. Add **GOOGLE_OAUTH_CLIENT_ID** and "
    "**GOOGLE_OAUTH_CLIENT_SECRET** in Settings → YouTube Studio (a *Desktop app* "
    "OAuth client from Google Cloud Console, with the YouTube Data API v3, YouTube "
    "Analytics API, YouTube Reporting API and Search Console API enabled), then "
    "connect your account under Settings → Google account."
)
NOT_CONNECTED = (
    "This needs a Google login — connect your account under Settings → Google "
    "account. The OAuth client is configured; nobody has signed in yet."
)


# ── storage ──────────────────────────────────────────────────────────────────

def _path(root: Path) -> Path:
    d = Path(root) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / _FILE


def _load(root: Path) -> dict:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(root: Path, data: dict) -> None:
    p = _path(root)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass          # no-op on Windows


def status(root: Path) -> dict:
    """Safe for the browser: no tokens, only what is connected and to what."""
    d = _load(root)
    granted = set((d.get("scope") or "").split())
    return {
        "client_configured": client_configured(),
        "connected": bool(d.get("refresh_token")),
        "channel_title": d.get("channel_title") or "",
        "channel_id": d.get("channel_id") or "",
        "connected_at": d.get("connected_at") or 0,
        "missing_scopes": [s.rsplit("/", 1)[-1] for s in SCOPES if granted and s not in granted],
        "redirect_uri": redirect_uri(),
        "reach_job_created": d.get("reach_job_created") or "",
        "reach_job_error": d.get("reach_job_error") or "",
    }


def update_meta(root: Path, **fields) -> None:
    """Record facts about the connection (e.g. the reach job) beside the token."""
    d = _load(root)
    if not d.get("refresh_token"):
        return
    d.update(fields)
    _save(root, d)


# ── HTTP seam (tests replace these two) ──────────────────────────────────────

async def _post_form(url: str, data: dict) -> tuple[int, dict]:
    import httpx
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, data=data)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": r.text[:300]}


async def _get_json(url: str, params: dict, token: str) -> tuple[int, dict]:
    import httpx
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": r.text[:300]}


# ── sign-in ──────────────────────────────────────────────────────────────────

def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def start() -> dict:
    """A consent URL for the browser. ``offline`` + ``consent`` so Google always
    returns a refresh token — without ``prompt=consent`` a second sign-in comes
    back without one and the login dies an hour later."""
    if not client_configured():
        raise NotConnected(NOT_CONFIGURED)
    from config import cfg

    now = time.time()
    for s in [s for s, p in _PENDING.items() if now - p["created"] > _PENDING_TTL]:
        _PENDING.pop(s, None)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    redirect = redirect_uri()
    _PENDING[state] = {"verifier": verifier, "redirect_uri": redirect, "created": now}
    query = urlencode({
        "client_id": cfg.google_oauth_client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    })
    return {"auth_url": f"{AUTH_URL}?{query}", "state": state, "redirect_uri": redirect}


def parse_response(text: str) -> tuple[str, str, str]:
    """(code, state, error) from a pasted redirect URL, a query string, or a bare code."""
    text = (text or "").strip()
    if not text:
        return "", "", ""
    if "code=" in text or "error=" in text:
        query = urlparse(text).query if "://" in text else text.lstrip("?")
        q = parse_qs(query)
        return (q.get("code", [""])[0], q.get("state", [""])[0], q.get("error", [""])[0])
    return text, "", ""


async def finish(root: Path, response: str) -> dict:
    """Exchange the code Google returned for a refresh token and store it."""
    if not client_configured():
        raise NotConnected(NOT_CONFIGURED)
    code, state, err = parse_response(response)
    if err:
        raise ValueError(f"Google refused the sign-in: {err}")
    if not code:
        raise ValueError("no authorization code in that — paste the whole address you landed on")
    if state:
        pending = _PENDING.pop(state, None)
        if not pending:
            raise ValueError("that sign-in was not started here or has expired — press Connect again")
    else:
        # A bare code carries no state; accept it only against the newest sign-in.
        if not _PENDING:
            raise ValueError("no sign-in in progress — press Connect first")
        state = max(_PENDING, key=lambda s: _PENDING[s]["created"])
        pending = _PENDING.pop(state)

    from config import cfg
    code_, body = await _post_form(TOKEN_URL, {
        "code": code,
        "client_id": cfg.google_oauth_client_id,
        "client_secret": cfg.google_oauth_client_secret,
        "redirect_uri": pending["redirect_uri"],
        "grant_type": "authorization_code",
        "code_verifier": pending["verifier"],
    })
    if code_ >= 400 or not body.get("access_token"):
        raise ValueError(f"Google would not exchange the code: "
                         f"{body.get('error_description') or body.get('error') or code_}")
    refresh = body.get("refresh_token")
    if not refresh:
        raise ValueError("Google returned no refresh token. Remove this app at "
                         "https://myaccount.google.com/permissions and connect again.")
    _ACCESS[refresh] = (body["access_token"], time.time() + float(body.get("expires_in") or 3600))

    title, cid = "", ""
    sc, ch = await _get_json(CHANNELS_URL, {"part": "snippet", "mine": "true"}, body["access_token"])
    if sc < 400 and ch.get("items"):
        cid = ch["items"][0].get("id", "")
        title = ch["items"][0].get("snippet", {}).get("title", "")
    _save(root, {"refresh_token": refresh, "scope": body.get("scope", ""),
                 "channel_id": cid, "channel_title": title, "connected_at": int(time.time())})
    return status(root)


async def access_token(root: Path) -> str:
    """A valid access token, refreshed when it is within a minute of expiring."""
    if not client_configured():
        raise NotConnected(NOT_CONFIGURED)
    refresh = _load(root).get("refresh_token")
    if not refresh:
        raise NotConnected(NOT_CONNECTED)
    cached = _ACCESS.get(refresh)
    if cached and cached[1] - 60 > time.time():
        return cached[0]
    from config import cfg
    code, body = await _post_form(TOKEN_URL, {
        "client_id": cfg.google_oauth_client_id,
        "client_secret": cfg.google_oauth_client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    })
    if code >= 400 or not body.get("access_token"):
        err = body.get("error") or f"HTTP {code}"
        if err == "invalid_grant":
            raise NotConnected(
                "The Google login has expired or was revoked — needs a new login under "
                "Settings → Google account. (An OAuth app left in *Testing* has its logins "
                "expired by Google after 7 days; set it to *In production* to stop that.)")
        raise NotConnected(f"Google would not refresh the login: {body.get('error_description') or err}")
    _ACCESS[refresh] = (body["access_token"], time.time() + float(body.get("expires_in") or 3600))
    return body["access_token"]


async def disconnect(root: Path) -> dict:
    """Revoke at Google (best effort) and forget the token locally."""
    refresh = _load(root).get("refresh_token")
    if refresh:
        try:
            await _post_form(REVOKE_URL, {"token": refresh})
        except Exception:
            pass      # forgetting it locally is what matters; revocation is courtesy
        _ACCESS.pop(refresh, None)
    try:
        _path(root).unlink()
    except OSError:
        pass
    return status(root)
