"""Several Reddit logins, not one.

``.env`` holds one value per key, so REDDIT_USERNAME could only ever describe a
single identity — fine for reading public feeds, useless the moment you want a
personal account and a project account to have separate subscriptions, saves and
posting rights.

Accounts live in ``data/reddit_accounts.json`` (0600), each with its own script-app
credentials. The legacy ``REDDIT_*`` env vars still work: they are surfaced as a
read-only account called "env" so an existing install keeps working untouched and
nothing has to be migrated by hand.

Passwords are stored in the clear, exactly as .env stores every other credential
here. That is a deliberate consistency choice, not an oversight — the file is
0600 and the threat model is the same one .env already has.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

_LOCK = threading.Lock()
_FILE = "reddit_accounts.json"

# The account synthesised from the legacy env vars. Reserved so a stored account
# can never shadow it and make the env credentials silently unreachable.
ENV_ID = "env"

FIELDS = ("client_id", "client_secret", "username", "password")


def _path(root: Path) -> Path:
    d = Path(root) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / _FILE


def _load_raw(root: Path) -> dict:
    try:
        return json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"accounts": [], "default": ""}


def _save_raw(root: Path, data: dict) -> None:
    p = _path(root)
    tmp = p.with_suffix(".tmp")
    # 0600 from creation rather than chmod after writing, which would leave the
    # passwords world-readable for the length of the write.
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


def _env_account() -> dict | None:
    from config import cfg

    if not all(getattr(cfg, f"reddit_{f}", "") for f in FIELDS):
        return None
    return {"id": ENV_ID, "label": f"{cfg.reddit_username} (.env)",
            "client_id": cfg.reddit_client_id,
            "client_secret": cfg.reddit_client_secret,
            "username": cfg.reddit_username,
            "password": cfg.reddit_password,
            "from_env": True}


def list_accounts(root: Path) -> list[dict]:
    """Every usable account, the legacy env one first when it exists."""
    out = []
    env = _env_account()
    if env:
        out.append(env)
    out += [a for a in _load_raw(root).get("accounts", []) if a.get("id") != ENV_ID]
    return out


def public_accounts(root: Path) -> list[dict]:
    """The same list with secrets removed — safe for the API and the UI."""
    default = default_id(root)
    return [{"id": a["id"], "label": a.get("label") or a.get("username") or a["id"],
             "username": a.get("username", ""),
             "from_env": bool(a.get("from_env")),
             "is_default": a["id"] == default}
            for a in list_accounts(root)]


def default_id(root: Path) -> str:
    """The account used when a tool call does not name one."""
    accounts = list_accounts(root)
    if not accounts:
        return ""
    stored = str(_load_raw(root).get("default") or "")
    if stored and any(a["id"] == stored for a in accounts):
        return stored
    return accounts[0]["id"]


def resolve(root: Path, ref: str = "") -> dict | None:
    """Find an account by id, label or username. Empty ``ref`` = the default.

    Matching on the username too because that is what a person calls the account
    — being made to look up an opaque id to post as yourself would be silly.
    """
    accounts = list_accounts(root)
    if not accounts:
        return None
    ref = (ref or "").strip()
    if not ref:
        return next((a for a in accounts if a["id"] == default_id(root)), accounts[0])
    low = ref.lower()
    for key in ("id", "label", "username"):
        hit = next((a for a in accounts if str(a.get(key, "")).lower() == low), None)
        if hit:
            return hit
    return None


def add_account(root: Path, label: str, *, client_id: str, client_secret: str,
                username: str, password: str) -> dict:
    label = (label or "").strip()[:60] or username.strip()[:60]
    missing = [f for f, v in (("client_id", client_id), ("client_secret", client_secret),
                              ("username", username), ("password", password)) if not str(v).strip()]
    if missing:
        # All four or nothing: a partial script app authenticates as no one and
        # fails at the first private endpoint rather than at setup.
        raise ValueError(f"a Reddit script app needs all four fields — missing: {', '.join(missing)}")
    with _LOCK:
        data = _load_raw(root)
        accounts = data.setdefault("accounts", [])
        if any(str(a.get("username", "")).lower() == username.strip().lower() for a in accounts):
            raise ValueError(f"an account for u/{username.strip()} already exists")
        acct = {"id": uuid.uuid4().hex[:8], "label": label,
                "client_id": client_id.strip(), "client_secret": client_secret.strip(),
                "username": username.strip(), "password": password,
                "created_at": int(time.time())}
        accounts.append(acct)
        if not data.get("default"):
            data["default"] = acct["id"]
        _save_raw(root, data)
    return acct


def update_account(root: Path, account_id: str, changes: dict) -> dict:
    if account_id == ENV_ID:
        raise ValueError("the .env account is configured in .env, not here")
    with _LOCK:
        data = _load_raw(root)
        acct = next((a for a in data.get("accounts", []) if a.get("id") == account_id), None)
        if not acct:
            raise KeyError(account_id)
        for key in ("label", *FIELDS):
            if changes.get(key):
                acct[key] = str(changes[key]).strip() if key != "password" else changes[key]
        _save_raw(root, data)
    return acct


def remove_account(root: Path, account_id: str) -> bool:
    if account_id == ENV_ID:
        raise ValueError("the .env account is removed by clearing REDDIT_* in .env")
    with _LOCK:
        data = _load_raw(root)
        before = len(data.get("accounts", []))
        data["accounts"] = [a for a in data.get("accounts", []) if a.get("id") != account_id]
        if len(data["accounts"]) == before:
            return False
        if data.get("default") == account_id:
            data["default"] = data["accounts"][0]["id"] if data["accounts"] else ""
        _save_raw(root, data)
    return True


def set_default(root: Path, account_id: str) -> str:
    if not any(a["id"] == account_id for a in list_accounts(root)):
        raise KeyError(account_id)
    with _LOCK:
        data = _load_raw(root)
        data["default"] = account_id
        _save_raw(root, data)
    return account_id
