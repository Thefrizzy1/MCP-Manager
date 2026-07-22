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
import threading
from pathlib import Path

from config import cfg, is_ui_writable_env_key

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_LOCK = threading.Lock()

# .env key -> cfg attribute to keep in sync within the writing process. Bools are
# coerced for the flag fields. (Cross-process sync is handled by readers that
# call read_env() directly, e.g. the bearer middleware.)
_CFG_SYNC: dict[str, str] = {
    "SSH_HOSTS": "ssh_hosts_json",
    "SMB_SHARES": "smb_shares_json",
    "MCP_BEARER_TOKEN": "mcp_bearer_token",
    "MCP_REQUIRE_BEARER": "mcp_require_bearer",
    "PUBLIC_MCP_BASE": "public_mcp_base",
    "MCP_LAN_HOST": "mcp_lan_host",
    "WEATHER_DEFAULT_LOCATION": "weather_default_location",
    "UI_USERNAME": "ui_username",
    "UI_PASSWORD": "ui_password",
}
_BOOL_KEYS = {"MCP_REQUIRE_BEARER"}


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
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _coerce(key: str, value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def update_env(updates: dict, *, validate: bool = True, path: Path | None = None) -> dict[str, str]:
    """Merge ``updates`` into .env and atomically rewrite. Returns the new env.

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

        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("# Plutus MCP Configuration\n\n")
            for k, v in env.items():
                f.write(f"{k}={v}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    # Keep the writing process's cfg snapshot consistent with disk.
    for key, attr in _CFG_SYNC.items():
        if key in updates and updates[key] is not None:
            value = updates[key]
            if key in _BOOL_KEYS:
                value = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes")
            else:
                value = _coerce(key, value)
            try:
                setattr(cfg, attr, value)
            except Exception:
                pass
    return env
