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
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
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
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())


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

        content = "# Plutus MCP Configuration\n\n" + "".join(f"{k}={v}\n" for k, v in env.items())
        _write_env_file(p, content)

    # Reflect the change onto os.environ + the cfg singleton so the running
    # process (probes, is_configured checks, tool calls) sees it immediately —
    # no restart. This now covers *every* service, not a hardcoded subset.
    apply_live_env(updates)
    return env
