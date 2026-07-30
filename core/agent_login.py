"""Web-based Claude Code login using a SESSION / OAuth token (never an API key).

The reliable, supported flow: the user runs ``claude setup-token`` on any machine
signed into their Claude plan (it does the browser handshake there and prints a
token), then pastes that token into the dashboard. We store it as
``CLAUDE_CODE_OAUTH_TOKEN`` in .env and agent_runner injects it into the
subprocess env — so runs draw from the subscription, applied immediately.

We deliberately do NOT try to drive the OAuth browser flow from inside the
container: it has no browser and the localhost callback isn't reachable, so an
"in-dashboard OAuth" button can't work. We never write ANTHROPIC_API_KEY here —
that is API billing and stays a compose-only opt-in.
"""
from __future__ import annotations

import time

from core.env_store import read_env, update_env

TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
# When the token was last saved. Needed because a mounted ~/.claude login and a
# saved token can *both* be stale, so neither can win unconditionally — see
# agent_runner.legacy_credential_source().
TOKEN_SAVED_KEY = "CLAUDE_CODE_OAUTH_TOKEN_SAVED_AT"


def token_present() -> bool:
    return bool((read_env().get(TOKEN_KEY, "") or "").strip())


def token_saved_at() -> int:
    try:
        return int((read_env().get(TOKEN_SAVED_KEY, "") or "0").strip() or 0)
    except ValueError:
        return 0


def save_token(token: str) -> dict:
    token = (token or "").strip().strip("'\"").strip()
    if not token:
        return {"ok": False, "error": "Empty token."}
    if token.startswith("sk-ant-api") and "oat" not in token:
        return {"ok": False, "error": "That looks like an API key, not a session token. Run `claude setup-token`."}
    update_env({TOKEN_KEY: token, TOKEN_SAVED_KEY: str(int(time.time()))})
    return {"ok": True}
