"""Keep a process's ``cfg`` in sync with ``.env`` edits made by the *other* process.

The UI and MCP servers run in separate processes, each with its own ``cfg``
singleton loaded at boot. ``env_store.update_env`` (the UI's writer) syncs the
*UI* process's cfg via ``apply_live_env`` — but the MCP tool process never hears
about it, so a credential configured in the dashboard kept reading as "not
configured" to the tools until a full restart. This closes that split-brain
(docs/ARCHITECTURE_AUDIT.md §5, D#3) for the MCP process without touching how any
tool reads ``cfg``.

Design:
- **mtime-gated + TTL.** At most one ``stat()`` every ``TTL`` seconds on the
  request path; a reload only happens when ``.env``'s mtime actually changes.
- **Lazy, inert first call.** The first call in a process just records the boot
  mtime (cfg already matches ``.env`` at boot) and applies nothing. A reload
  only fires on a *later* change — so in a test suite, where ``.env`` doesn't
  change mid-run, this is a no-op and never calls ``read_env`` on the hot path
  (which some tests stub).
- **Adds, changes and clears propagate.** A key removed from ``.env`` since the
  last apply is reset to "" on cfg, matching ``update_env``'s clear semantics.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware

from config import apply_live_env, is_ui_writable_env_key
from core.env_store import ENV_PATH, read_env

TTL_SECONDS = 3.0

_lock = threading.Lock()
# mtime None => not yet initialised; seen None => no reload has applied yet.
_state: dict = {"checked": 0.0, "mtime": None, "seen": None}


def _reset_state_for_tests() -> None:
    _state.update(checked=0.0, mtime=None, seen=None)


def refresh_cfg_from_env_if_changed(path: Path | None = None, *, ttl: float = TTL_SECONDS) -> bool:
    """Reload ``cfg`` from ``.env`` if the file changed since the last check.

    Returns True iff a reload actually applied. Safe to call on every request:
    it stats one file at most once per ``ttl`` and only reads/applies on a real
    change.
    """
    p = path or ENV_PATH
    now = time.time()
    with _lock:
        if _state["mtime"] is not None and (now - _state["checked"]) < ttl:
            return False
        _state["checked"] = now
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return False

        if _state["mtime"] is None:
            # First call: cfg already reflects .env from boot. Record the mtime
            # and apply nothing (and, deliberately, do not read the file — so a
            # test that stubs read_env is never touched on this path).
            _state["mtime"] = mtime
            return False

        if mtime == _state["mtime"]:
            return False
        _state["mtime"] = mtime

        try:
            env = read_env(p)
        except Exception:
            return False
        updates = {k: v for k, v in env.items() if is_ui_writable_env_key(k)}
        # Clears: keys we applied on a previous reload that are gone now.
        for k in (_state["seen"] or set()):
            if k not in updates:
                updates[k] = ""       # reset on cfg, matching update_env's clear
        _state["seen"] = {k for k, v in updates.items() if v != ""}
        try:
            apply_live_env(updates)
        except Exception:
            return False
        return True


class LiveConfigMiddleware(BaseHTTPMiddleware):
    """Refresh this process's cfg from .env before handling each MCP request, so
    a credential set in the UI reaches the tools within TTL_SECONDS — no restart.
    A refresh failure never affects the request."""

    async def dispatch(self, request, call_next):
        try:
            refresh_cfg_from_env_if_changed()
        except Exception:
            pass
        return await call_next(request)
