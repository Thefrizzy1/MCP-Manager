"""Minimal OAuth 2.1 provider so Plutus works as a first-class MCP connector
(e.g. claude.ai custom connectors), which require OAuth rather than a static
bearer token.

Scope on purpose: a single-user, self-hosted authorization server that is also
the resource server. Public clients only (PKCE, no client secret) — exactly what
MCP clients use. Opaque tokens stored server-side (revocable, simple), pruned on
access. The user authenticates at /authorize with the existing Plutus UI
credentials.

Implements the pieces MCP clients discover and use:
  - RFC 9728 Protected Resource Metadata
  - RFC 8414 Authorization Server Metadata
  - RFC 7591 Dynamic Client Registration (public clients)
  - OAuth 2.1 authorization-code grant with PKCE (S256), refresh tokens

Everything here is pure logic over a JSON file store — no web framework — so it
is unit-testable without a server. The HTTP wiring lives in ui/runtime.py.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

_LOCK = threading.Lock()

CODE_TTL = 300          # authorization code: 5 minutes, single use
TOKEN_TTL = 30 * 24 * 3600   # access token: 30 days
REFRESH_TTL = 180 * 24 * 3600  # refresh token: 180 days
DEFAULT_SCOPE = "mcp"


# ── storage ───────────────────────────────────────────────────────────────────

def _dir(root: Path) -> Path:
    d = Path(root) / "data" / "oauth"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(root: Path, name: str) -> dict:
    p = _dir(root) / f"{name}.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(root: Path, name: str, data: dict) -> None:
    p = _dir(root) / f"{name}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ── PKCE ──────────────────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def verify_pkce(verifier: str, challenge: str, method: str = "S256") -> bool:
    verifier = (verifier or "").strip()
    challenge = (challenge or "").strip()
    if not verifier or not challenge:
        return False
    if (method or "S256").upper() == "PLAIN":
        return secrets.compare_digest(verifier, challenge)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return secrets.compare_digest(_b64url(digest), challenge)


# ── dynamic client registration (RFC 7591, public clients) ────────────────────

def register_client(root: Path, body: dict) -> dict:
    """Register a public client. Accepts the client's redirect_uris and name;
    issues a client_id. No client_secret (PKCE-protected public client)."""
    redirect_uris = [str(u).strip() for u in (body.get("redirect_uris") or []) if str(u).strip()]
    if not redirect_uris:
        raise ValueError("redirect_uris is required")
    for u in redirect_uris:
        if not (u.startswith("https://") or u.startswith("http://localhost") or u.startswith("http://127.0.0.1")):
            raise ValueError(f"redirect_uri must be https (or localhost): {u!r}")
    client_id = "plutus-" + secrets.token_urlsafe(16)
    record = {
        "client_id": client_id,
        "client_name": str(body.get("client_name") or "MCP client")[:120],
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "created_at": int(time.time()),
    }
    with _LOCK:
        clients = _load(root, "clients")
        clients[client_id] = record
        _save(root, "clients", clients)
    return record


def get_client(root: Path, client_id: str) -> dict | None:
    return _load(root, "clients").get((client_id or "").strip())


# ── authorization codes ───────────────────────────────────────────────────────

def _prune(store: dict, ttl_field: str = "expires_at") -> dict:
    now = time.time()
    return {k: v for k, v in store.items() if float(v.get(ttl_field, 0)) > now}


def issue_code(root: Path, *, client_id: str, redirect_uri: str, code_challenge: str,
               code_challenge_method: str = "S256", scope: str = DEFAULT_SCOPE,
               resource: str = "") -> str:
    code = secrets.token_urlsafe(32)
    with _LOCK:
        codes = _prune(_load(root, "codes"))
        codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": (code_challenge_method or "S256").upper(),
            "scope": scope or DEFAULT_SCOPE,
            "resource": resource,
            "expires_at": time.time() + CODE_TTL,
        }
        _save(root, "codes", codes)
    return code


def _consume_code(root: Path, code: str) -> dict | None:
    with _LOCK:
        codes = _prune(_load(root, "codes"))
        rec = codes.pop((code or "").strip(), None)
        _save(root, "codes", codes)  # single-use: always remove
    return rec


# ── tokens ────────────────────────────────────────────────────────────────────

def _new_token_set(root: Path, *, client_id: str, scope: str) -> dict:
    access = "mcp_at_" + secrets.token_urlsafe(32)
    refresh = "mcp_rt_" + secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        tokens = _prune(_load(root, "tokens"))
        tokens[access] = {"client_id": client_id, "scope": scope, "expires_at": now + TOKEN_TTL}
        _save(root, "tokens", tokens)
        refreshes = _prune(_load(root, "refresh"))
        refreshes[refresh] = {"client_id": client_id, "scope": scope, "expires_at": now + REFRESH_TTL}
        _save(root, "refresh", refreshes)
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": TOKEN_TTL,
        "refresh_token": refresh,
        "scope": scope,
    }


def exchange_code(root: Path, *, code: str, code_verifier: str, client_id: str,
                  redirect_uri: str) -> dict:
    """Authorization-code + PKCE exchange. Raises ValueError on any mismatch."""
    rec = _consume_code(root, code)
    if not rec:
        raise ValueError("invalid_grant: unknown or expired code")
    if rec["client_id"] != (client_id or "").strip():
        raise ValueError("invalid_grant: client mismatch")
    if rec["redirect_uri"] != (redirect_uri or "").strip():
        raise ValueError("invalid_grant: redirect_uri mismatch")
    if not verify_pkce(code_verifier, rec["code_challenge"], rec["code_challenge_method"]):
        raise ValueError("invalid_grant: PKCE verification failed")
    return _new_token_set(root, client_id=rec["client_id"], scope=rec["scope"])


def refresh_token(root: Path, *, refresh: str, client_id: str) -> dict:
    """Refresh-token grant with rotation.

    OAuth 2.1 requires rotation for public clients: the presented token is
    single-use and is consumed here. Leaving it valid (as this did) meant a
    stolen refresh token stayed replayable for its full 180-day TTL, and the
    store grew a new entry on every refresh without ever dropping one.
    """
    presented = (refresh or "").strip()
    with _LOCK:
        refreshes = _prune(_load(root, "refresh"))
        rec = refreshes.pop(presented, None)
        if rec is not None:
            _save(root, "refresh", refreshes)
    if not rec:
        raise ValueError("invalid_grant: unknown or expired refresh token")
    if rec["client_id"] != (client_id or "").strip():
        raise ValueError("invalid_grant: client mismatch")
    return _new_token_set(root, client_id=rec["client_id"], scope=rec["scope"])


def revoke_token(root: Path, token: str) -> bool:
    """RFC 7009-style revocation: drop an access or refresh token. Returns True if
    something was removed. Lets a compromised connector be cut off without
    hand-deleting data/oauth/*.json."""
    t = (token or "").strip()
    if not t:
        return False
    removed = False
    with _LOCK:
        for name in ("tokens", "refresh"):
            store = _load(root, name)
            if store.pop(t, None) is not None:
                _save(root, name, store)
                removed = True
    if removed:
        _ACCESS_CACHE.pop(t, None)
    return removed


# Access-token validation runs on *every* authenticated MCP request. Reading and
# parsing the whole token file each time is a synchronous disk hit on the event
# loop; cache the verdict briefly instead. TTL is short so a revoked token stops
# working promptly, and revoke_token() evicts immediately.
_ACCESS_TTL = 5.0
_ACCESS_CACHE: dict[str, tuple[float, bool]] = {}


def validate_access_token(root: Path, token: str) -> bool:
    token = (token or "").strip()
    if not token:
        return False
    now = time.time()
    hit = _ACCESS_CACHE.get(token)
    if hit and hit[0] > now:
        return hit[1]
    rec = _load(root, "tokens").get(token)
    ok = bool(rec) and float(rec.get("expires_at", 0)) > now
    if len(_ACCESS_CACHE) > 512:          # bound it: tokens are attacker-suppliable
        _ACCESS_CACHE.clear()
    _ACCESS_CACHE[token] = (now + _ACCESS_TTL, ok)
    return ok


# ── discovery metadata ────────────────────────────────────────────────────────

def protected_resource_metadata(base: str, resource_url: str) -> dict:
    base = base.rstrip("/")
    return {
        "resource": resource_url,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [DEFAULT_SCOPE],
    }


def authorization_server_metadata(base: str) -> dict:
    base = base.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [DEFAULT_SCOPE],
    }


def build_redirect(redirect_uri: str, params: dict) -> str:
    sep = "&" if "?" in redirect_uri else "?"
    return redirect_uri + sep + urlencode({k: v for k, v in params.items() if v is not None})


def redirect_uri_registered(client: dict, redirect_uri: str) -> bool:
    return (redirect_uri or "").strip() in (client.get("redirect_uris") or [])


def check_user_credentials(username: str, password: str, *, expected_user: str, expected_pass: str) -> bool:
    """Constant-time check of the Plutus UI login used to authorize a connection."""
    u = secrets.compare_digest((username or "").strip(), (expected_user or "").strip())
    p = secrets.compare_digest(password or "", expected_pass or "")
    return u and p


def load_metadata_context(root: Path) -> dict[str, Any]:
    """Small helper for tests/inspection: counts of live artifacts."""
    return {
        "clients": len(_load(root, "clients")),
        "codes": len(_prune(_load(root, "codes"))),
        "tokens": len(_prune(_load(root, "tokens"))),
    }
