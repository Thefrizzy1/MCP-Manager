"""Multi-user store and signed sessions for the Web UI.

Replaces the single hard-coded ``UI_USERNAME``/``UI_PASSWORD`` with a small user
table (``data/ui_users.json``) so the dashboard can have several accounts and an
admin who manages them — while a stateless, HMAC-signed session cookie lets the
browser stay logged in without a server-side session store (nothing to lose on a
UI-process restart).

Design constraints honoured here:

- **Stdlib only.** Passwords are PBKDF2-HMAC-SHA256 with a per-user salt; sessions
  are signed with ``hmac``. No new dependency.
- **Back-compat.** An existing ``.env`` ``UI_PASSWORD`` still works: on first run
  the admin is seeded from it. Only when no password is configured at all do we
  seed the ``admin`` / ``adminadmin`` default (flagged so the UI can nag until it
  is changed) — this is what unbroke the "random password printed once, then a
  401 loop" lockout.
- **Store wins for known users.** Once a username exists in the store, only its
  stored password authenticates it; a stale ``.env`` value never lingers.

Only the UI process needs this; the MCP transport authenticates with its bearer
token, not UI sessions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from core.atomic_json import read_json, write_json

USERS_FILE = "ui_users.json"
_PBKDF2_ROUNDS = 200_000
_DEFAULT_USERNAME = "admin"
_DEFAULT_PASSWORD = "adminadmin"

# Session lifetimes. "Remember me" persists across browser restarts; the plain
# session still carries an expiry so a leaked cookie cannot live forever.
SESSION_TTL = 12 * 3600
REMEMBER_TTL = 30 * 24 * 3600

ROLES = ("admin", "user")


def _path(root: Path) -> Path:
    return Path(root) / "data" / USERS_FILE


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ── password hashing ─────────────────────────────────────────────────────────

def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    """Return ``(salt_hex, hash_hex)`` for ``password`` (new salt unless given)."""
    salt_hex = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt_hex), _PBKDF2_ROUNDS)
    return salt_hex, dk.hex()


def _password_ok(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        _, got = hash_password(password, salt=salt_hex)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, hash_hex)


# ── store I/O ────────────────────────────────────────────────────────────────

def _empty_store() -> dict:
    return {"version": 1, "session_secret": secrets.token_urlsafe(32), "users": []}


def _load(root: Path) -> dict:
    data = read_json(_path(root), None)
    if not isinstance(data, dict) or "users" not in data:
        return _empty_store()
    data.setdefault("session_secret", secrets.token_urlsafe(32))
    data.setdefault("users", [])
    return data


def _save(root: Path, data: dict) -> None:
    write_json(_path(root), data)


def _find(data: dict, username: str) -> dict | None:
    u = (username or "").strip()
    return next((x for x in data["users"] if x.get("username") == u), None)


# ── seeding ──────────────────────────────────────────────────────────────────

def ensure_seed(root: Path) -> dict:
    """Guarantee at least one admin exists. Idempotent.

    Returns ``{"seeded": bool, "username": str, "default_password": str|None}``.
    ``default_password`` is set only when we minted the ``admin``/``adminadmin``
    default, so the caller (boot banner / UI banner) can tell the operator to
    change it.
    """
    from config import cfg

    data = _load(root)
    if data["users"]:
        return {"seeded": False, "username": data["users"][0]["username"], "default_password": None}

    env_user = (cfg.ui_username or _DEFAULT_USERNAME).strip() or _DEFAULT_USERNAME
    env_pw = (cfg.ui_password or "").strip()
    if env_pw:
        # Adopt the operator's existing credential; nothing to nag about.
        salt, digest = hash_password(env_pw)
        data["users"].append(_make_user(env_user, salt, digest, role="admin", is_default=False))
        _save(root, data)
        return {"seeded": True, "username": env_user, "default_password": None}

    salt, digest = hash_password(_DEFAULT_PASSWORD)
    data["users"].append(_make_user(_DEFAULT_USERNAME, salt, digest, role="admin", is_default=True))
    _save(root, data)
    return {"seeded": True, "username": _DEFAULT_USERNAME, "default_password": _DEFAULT_PASSWORD}


def _make_user(username: str, salt: str, digest: str, *, role: str, is_default: bool) -> dict:
    return {
        "username": username, "salt": salt, "hash": digest,
        "role": role if role in ROLES else "user",
        "must_change": is_default, "is_default": is_default,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ── credential verification ──────────────────────────────────────────────────

def verify_credentials(root: Path, username: str, password: str) -> dict | None:
    """Return the user record on success, else ``None``.

    A username present in the store authenticates *only* against the store. A
    username absent from the store falls back to the ``.env`` admin credential
    (``UI_USERNAME``/``UI_PASSWORD``) for back-compat with pre-store deployments.
    """
    from config import allow_empty_ui_password, cfg

    ensure_seed(root)
    data = _load(root)
    rec = _find(data, username)
    if rec is not None:
        if _password_ok(password, rec.get("salt", ""), rec.get("hash", "")):
            return rec
        return None

    # Not in the store — env fallback.
    if cfg.ui_username and username == cfg.ui_username:
        if cfg.ui_password and secrets.compare_digest(password, cfg.ui_password):
            return {"username": username, "role": "admin", "must_change": False, "is_default": False}
        if not cfg.ui_password and allow_empty_ui_password() and not password:
            return {"username": username, "role": "admin", "must_change": False, "is_default": False}
    return None


def auth_available(root: Path) -> bool:
    """True when some credential can authenticate. Drives the 503 gate.

    Ensures the default seed first, so this is effectively always True (Plutus
    always has a login now) except on a catastrophic store-write failure — which
    is exactly when a 503 is the honest answer.
    """
    from config import allow_empty_ui_password, cfg

    if allow_empty_ui_password():
        return True
    ensure_seed(root)
    data = _load(root)
    return bool(data["users"]) or bool(cfg.ui_password)


# ── user management ──────────────────────────────────────────────────────────

def list_users(root: Path) -> list[dict]:
    """Public view of the users (no salt/hash)."""
    ensure_seed(root)
    data = _load(root)
    return [
        {"username": u["username"], "role": u.get("role", "user"),
         "must_change": bool(u.get("must_change")), "is_default": bool(u.get("is_default")),
         "created": u.get("created", "")}
        for u in data["users"]
    ]


def add_user(root: Path, username: str, password: str, role: str = "user") -> dict:
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters.")
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}.")
    data = _load(root)
    if _find(data, username):
        raise ValueError(f"User '{username}' already exists.")
    salt, digest = hash_password(password)
    data["users"].append(_make_user(username, salt, digest, role=role, is_default=False))
    _save(root, data)
    return {"username": username, "role": role}


def remove_user(root: Path, username: str) -> None:
    data = _load(root)
    rec = _find(data, username)
    if not rec:
        raise ValueError(f"User '{username}' not found.")
    admins = [u for u in data["users"] if u.get("role") == "admin"]
    if rec.get("role") == "admin" and len(admins) <= 1:
        raise ValueError("Cannot remove the last admin.")
    data["users"] = [u for u in data["users"] if u.get("username") != username]
    _save(root, data)


def set_password(root: Path, username: str, new_password: str) -> None:
    if len(new_password or "") < 8:
        raise ValueError("Password must be at least 8 characters.")
    data = _load(root)
    rec = _find(data, username)
    if not rec:
        raise ValueError(f"User '{username}' not found.")
    rec["salt"], rec["hash"] = hash_password(new_password)
    rec["must_change"] = False
    rec["is_default"] = False
    _save(root, data)


def set_role(root: Path, username: str, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}.")
    data = _load(root)
    rec = _find(data, username)
    if not rec:
        raise ValueError(f"User '{username}' not found.")
    if rec.get("role") == "admin" and role != "admin":
        admins = [u for u in data["users"] if u.get("role") == "admin"]
        if len(admins) <= 1:
            raise ValueError("Cannot demote the last admin.")
    rec["role"] = role
    _save(root, data)


def get_user(root: Path, username: str) -> dict | None:
    data = _load(root)
    rec = _find(data, username)
    if not rec:
        return None
    return {"username": rec["username"], "role": rec.get("role", "user"),
            "must_change": bool(rec.get("must_change")), "is_default": bool(rec.get("is_default"))}


def default_password_active(root: Path) -> bool:
    """True while any account is still on a seeded default password — the UI shows
    a persistent 'change the default password' banner until this is false."""
    data = _load(root)
    return any(u.get("is_default") for u in data["users"])


# ── signed sessions ──────────────────────────────────────────────────────────

def _secret(root: Path) -> bytes:
    return _load(root)["session_secret"].encode("utf-8")


def sign_session(root: Path, username: str, *, remember: bool = False) -> str:
    """A stateless ``payload.signature`` token. Rotating the store's
    ``session_secret`` invalidates every outstanding session."""
    ttl = REMEMBER_TTL if remember else SESSION_TTL
    payload = {"u": username, "iat": int(time.time()), "exp": int(time.time()) + ttl}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_secret(root), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session(root: Path, token: str) -> str | None:
    """Return the username for a valid, unexpired token whose user still exists."""
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expected = hmac.new(_secret(root), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    username = payload.get("u", "")
    data = _load(root)
    if _find(data, username) is None:
        # Fall back to the env admin (store may be empty on a pre-store box).
        from config import cfg
        if not (cfg.ui_username and username == cfg.ui_username):
            return None
    return username
