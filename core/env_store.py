"""Single source of truth for reading and writing the ``.env`` file.

Historically two writers existed — ``save_env`` in main.py and ``_save_env_key``
in tools/ssh_smb.py — with different validation, locking, and formatting. That
divergence was both a maintenance smell and a latent corruption race. This module
is the one canonical implementation:

- ``read_env()`` — parse ``.env`` into a dict (UTF-8, latin-1 fallback).
- ``update_env(updates)`` — validate, merge, and atomically rewrite, then sync the
  relevant in-memory ``cfg`` fields so the *current* process sees the change.

It also underpins live bearer-auth: the MCP process (a separate process from the
UI) reads the token/flag straight from here at request time, so toggling auth in
the UI takes effect without a restart. See core/mcp_bearer_middleware.py.
"""
from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from config import apply_live_env, is_ui_writable_env_key

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_LOCK = threading.Lock()


def read_env(path: Path | None = None) -> dict[str, str]:
    """Parse the .env file into {KEY: value}. Missing file -> {}."""
    p = path or ENV_PATH
    env: dict[str, str] = {}
    if not p.exists():
        return env
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="latin-1")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        # Tolerate the two shapes people actually paste in. Without this a
        # quoted token round-trips with its quotes attached and silently fails
        # every comparison that uses it (e.g. the MCP bearer check).
        if key.startswith("export "):
            key = key[len("export "):].strip()
        val = v.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        env[key] = val
    return env


def _coerce(key: str, value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _write_env_file(p: Path, content: str) -> None:
    """Persist .env. Prefer an atomic tmp-file + rename, but fall back to an
    in-place overwrite when that fails — a Docker single-file bind mount
    (``./.env:/app/.env``) is a mount point that cannot be replaced via rename
    (EXDEV/EBUSY), which would otherwise 500 every settings save."""
    tmp = p.with_name(p.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        return
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

    # Fallback: truncate-in-place. Under Docker's single-file bind mount this is
    # the *normal* path, not an edge case — so keep a copy first. A crash between
    # truncate and write would otherwise destroy every credential in .env.
    try:
        if p.exists():
            bak = p.with_name(p.name + ".bak")
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def _append_env(path: Path, key: str, value: str) -> None:
    """Add one key by appending, leaving the rest of the file byte-for-byte alone.

    ``update_env`` rewrites .env from the parsed dict, which drops every comment
    and blank line. That is acceptable when someone clicks Save in Settings — it
    is their edit — but the first-run password is written *unprompted*, and
    silently reformatting a file the operator hand-wrote is not a side effect a
    boot should have. Appending keeps their comments.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(f"Values cannot contain newlines ({key})")
    with _LOCK:
        existing = ""
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                existing = path.read_text(encoding="latin-1")
        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{prefix}{key}={value}\n")
            f.flush()
            os.fsync(f.fileno())
    apply_live_env({key: value})
    os.environ[key] = value


def ensure_ui_password(path: Path | None = None) -> tuple[str, bool]:
    """Guarantee a UI password exists. Returns ``(password, generated_now)``.

    Called once at boot. Plutus used to fall back to a constant compiled into the
    source and print it in the banner, which is a published credential for a
    dashboard that can reach Docker, SSH and the filesystem. Instead the first run
    mints a random password and persists it, so the value is unique per install
    and still discoverable — it is printed once, and lives in .env afterwards.

    If .env cannot be written the generated password is kept for this process
    only: still unguessable, but it changes on restart, and the caller says so
    rather than pretending it was saved.
    """
    p = path or ENV_PATH
    existing = (os.getenv("UI_PASSWORD") or read_env(p).get("UI_PASSWORD") or "").strip()
    if existing:
        return existing, False
    pw = secrets.token_urlsafe(12)
    try:
        _append_env(p, "UI_PASSWORD", pw)
    except Exception:
        # Keep it live in-process even when persistence fails, so the UI works
        # this session instead of 503-ing on a fresh box with a read-only mount.
        os.environ["UI_PASSWORD"] = pw
        apply_live_env({"UI_PASSWORD": pw})
    return pw, True


def ui_password_persisted(path: Path | None = None) -> bool:
    """True when the password is actually written down, not just in this process."""
    return bool(read_env(path or ENV_PATH).get("UI_PASSWORD", "").strip())


def update_env(updates: dict, *, validate: bool = True, path: Path | None = None) -> dict[str, str]:
    """Merge ``updates`` into .env and atomically rewrite. Returns the new env.

    Key semantics, which callers depend on:
      - key absent from ``updates``, or value ``None`` -> leave as-is.
      - value ``""`` -> **delete the key**. This is how a credential gets revoked
        from the UI. Previously an empty value was silently skipped, so a leaked
        API key could be set but never removed without editing .env by hand.

    Raises ValueError on a disallowed key name, an embedded newline, or an empty
    UI_PASSWORD. ``validate=False`` is for trusted internal writers (e.g. JSON
    blobs for SSH_HOSTS) that still want the atomic write + cfg sync.
    """
    p = path or ENV_PATH
    with _LOCK:
        env = read_env(p)
        for raw_key, raw_val in updates.items():
            if raw_val is None:
                continue
            key = str(raw_key).strip()
            if validate and not is_ui_writable_env_key(key):
                raise ValueError(f"Invalid or disallowed environment variable name: {key!r}")
            val = _coerce(key, raw_val)
            if "\n" in val or "\r" in val:
                raise ValueError(f"Values cannot contain newlines ({key})")
            if key == "UI_PASSWORD" and not val:
                raise ValueError("UI_PASSWORD cannot be empty")
            if val:
                env[key] = val
            else:
                env.pop(key, None)   # explicit "" clears; omit the key to keep it

        content = "# Plutus MCP Configuration\n\n" + "".join(f"{k}={v}\n" for k, v in env.items())
        _write_env_file(p, content)

    # Reflect the change onto os.environ + the cfg singleton so the running
    # process (probes, is_configured checks, tool calls) sees it immediately —
    # no restart. This now covers *every* service, not a hardcoded subset.
    apply_live_env(updates)
    return env
