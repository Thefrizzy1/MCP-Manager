"""Web-based Claude Code login using a SESSION / OAuth token (never an API key).

Two paths, both landing a subscription OAuth token in .env as
``CLAUDE_CODE_OAUTH_TOKEN`` (which agent_runner injects into the subprocess env):

1. ``save_token`` — the reliable path: the user runs ``claude setup-token`` on any
   machine signed into their Claude plan and pastes the token into the dashboard.
2. ``start_interactive`` / ``finish_interactive`` — spawn ``claude setup-token`` in
   the container, surface the OAuth URL to click, and feed the pasted code back on
   stdin. Version-dependent and fragile; it degrades to path 1 on any hiccup.

We never write ANTHROPIC_API_KEY here — that is API billing and stays compose-only.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

from core.env_store import read_env, update_env

TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"

_URL_RE = re.compile(r"https://\S+")
# Rough shape of a Claude Code OAuth token; used only to pluck it from output.
_TOKEN_RE = re.compile(r"(sk-ant-oat[\w-]+|[A-Za-z0-9_\-]{40,})")

_login: dict = {"proc": None, "lines": [], "url": None, "started": 0.0}
_lock = threading.Lock()


def token_present() -> bool:
    return bool((read_env().get(TOKEN_KEY, "") or "").strip())


def save_token(token: str) -> dict:
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "Empty token."}
    if token.startswith(("sk-ant-api", "sk-ant-api03")) and "oat" not in token:
        return {"ok": False, "error": "That looks like an API key, not a session token. Use `claude setup-token`."}
    update_env({TOKEN_KEY: token})
    return {"ok": True}


def _drain(proc) -> None:
    for line in proc.stdout:
        with _lock:
            _login["lines"].append(line.rstrip())
            if _login["url"] is None:
                m = _URL_RE.search(line)
                if m:
                    _login["url"] = m.group(0)


def start_interactive() -> dict:
    """Spawn `claude setup-token` and return the OAuth URL to click."""
    with _lock:
        if _login["proc"] and _login["proc"].poll() is None:
            _login["proc"].kill()
        _login.update(proc=None, lines=[], url=None, started=time.time())
    try:
        proc = subprocess.Popen(
            ["claude", "setup-token"], text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "`claude` not found in the container."}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    _login["proc"] = proc
    threading.Thread(target=_drain, args=(proc,), daemon=True).start()
    # Give it a moment to print the URL.
    for _ in range(40):
        with _lock:
            if _login["url"]:
                return {"ok": True, "url": _login["url"]}
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    return {"ok": False, "error": "Could not detect an OAuth URL from `claude setup-token`. "
            "Use the token box instead (run `claude setup-token` yourself and paste it)."}


def finish_interactive(code: str) -> dict:
    """Feed the pasted code to the waiting process and persist the resulting token."""
    proc = _login.get("proc")
    if not proc:
        return {"ok": False, "error": "No login in progress. Start again."}
    try:
        if proc.poll() is None and code:
            proc.stdin.write(code.strip() + "\n")
            proc.stdin.flush()
    except Exception as e:
        return {"ok": False, "error": f"Could not send the code: {e}"}
    # Wait for completion and scrape a token from the output.
    for _ in range(60):
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    with _lock:
        text = "\n".join(_login["lines"])
    for m in _TOKEN_RE.finditer(text):
        cand = m.group(0)
        if cand.startswith("sk-ant-oat") or (len(cand) >= 40 and "http" not in cand):
            return save_token(cand)
    return {"ok": False, "error": "Finished, but no token was found in the output. "
            "Use the token box instead."}
