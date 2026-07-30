"""AI provider surface: CLI runtimes, their accounts, logins and capability tests.

One provider = one CLI (Claude Code, Codex). One account = one credentials
directory that the CLI is pointed at via its config-dir env var. See
core/ai_providers for why that, rather than a bearer token, is the unit of auth.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import agent_runner, ai_providers, provider_login
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])


def _known(pid: str) -> dict:
    if pid not in ai_providers.PROVIDERS:
        raise HTTPException(404, f"unknown provider '{pid}'")
    return ai_providers.PROVIDERS[pid]


def _known_account(pid: str, aid: str) -> dict:
    _known(pid)
    acct = ai_providers.get_account(ROOT, pid, aid)
    if not acct:
        raise HTTPException(404, "account not found")
    return acct


@router.get("/api/v1/providers")
async def api_providers():
    return {"providers": await asyncio.to_thread(ai_providers.all_status, ROOT),
            "guided_login_available": provider_login.available()}


@router.get("/api/v1/providers/{pid}/models")
async def api_provider_models(pid: str, account_id: str = ""):
    """The models this provider — and, where it can be asked, this *account* — offers.

    The launch wizard used to hardcode Opus/Sonnet/Haiku, which are meaningless
    to Codex and Gemini: picking a Codex account still offered you Claude models.
    An HTTP provider can be asked what it actually serves, so Gemini's list is
    live; a CLI's is the curated menu plus a free-text field, because CLIs gain
    and lose model ids between releases.
    """
    _known(pid)
    if account_id:
        _known_account(pid, account_id)
    return {"provider": pid,
            **await asyncio.to_thread(ai_providers.list_models, ROOT, pid, account_id)}


class AccountBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=60)


@router.post("/api/v1/providers/{pid}/accounts")
async def api_add_account(pid: str, body: AccountBody):
    _known(pid)
    try:
        acct = ai_providers.add_account(ROOT, pid, body.label)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "account": acct,
            "providers": await asyncio.to_thread(ai_providers.all_status, ROOT)}


@router.delete("/api/v1/providers/{pid}/accounts/{aid}")
async def api_remove_account(pid: str, aid: str):
    _known_account(pid, aid)
    ai_providers.remove_account(ROOT, pid, aid)
    return {"ok": True, "providers": await asyncio.to_thread(ai_providers.all_status, ROOT)}


@router.post("/api/v1/providers/{pid}/accounts/{aid}/logout")
async def api_logout_account(pid: str, aid: str):
    _known_account(pid, aid)
    dropped = ai_providers.logout_account(ROOT, pid, aid)
    return {"ok": True, "credentials_removed": dropped,
            "providers": await asyncio.to_thread(ai_providers.all_status, ROOT)}


@router.post("/api/v1/providers/{pid}/accounts/{aid}/adopt")
async def api_adopt_login(pid: str, aid: str):
    """Claim the CLI's current login for this account.

    Needed because not every CLI can be pointed at a per-account directory —
    Gemini reads ~/.gemini and offers no override. For those, you log in as one
    identity, adopt it here, log out of the CLI, log in as the next, and adopt that
    into another account.
    """
    _known_account(pid, aid)
    res = await asyncio.to_thread(ai_providers.adopt_login, ROOT, pid, aid)
    if not res["ok"]:
        raise HTTPException(400, res["error"])
    return {"ok": True, "copied": res["copied"], "from": res["from"],
            "providers": await asyncio.to_thread(ai_providers.all_status, ROOT)}


@router.post("/api/v1/providers/{pid}/accounts/{aid}/test")
async def api_test_account(pid: str, aid: str, with_mcp: bool = False):
    """Real capability test: executes a prompt through the CLI, optionally with
    Plutus's own MCP config attached. Not an HTTP ping."""
    _known_account(pid, aid)
    mcp_path, url, token = None, "", ""
    if with_mcp:
        from ui.runtime import _agent_mcp_target
        url, token = _agent_mcp_target()
        # Claude's check runs through --mcp-config; the others end at the same
        # endpoint by a different road, so they get the url instead of a file.
        if pid == "claude":
            mcp_path = agent_runner.write_plutus_mcp_config(ROOT, mcp_url=url, token=token)
    res = await asyncio.to_thread(
        ai_providers.capability_test, ROOT, pid, aid,
        mcp_config_path=mcp_path, mcp_url=url, mcp_token=token,
    )
    return {"ok": res["ok"], "checks": res["checks"]}


# ── guided login ─────────────────────────────────────────────────────────────

@router.get("/api/v1/providers/login")
async def api_login_status():
    return provider_login.FLOW.snapshot()


@router.post("/api/v1/providers/{pid}/accounts/{aid}/login/start")
async def api_login_start(pid: str, aid: str, use_token_flow: bool = True):
    """Begin the interactive login for one account.

    ``use_token_flow`` drives `claude setup-token` (prints a long-lived token);
    otherwise the plain interactive `claude` login, which writes credentials
    straight into the account's config dir.
    """
    spec = _known(pid)
    _known_account(pid, aid)
    if spec.get("kind") == ai_providers.KIND_API:
        raise HTTPException(400, f"{spec['label']} authenticates with an API key, not a "
                                 f"login. {spec.get('key_hint', '')}".strip())
    cmd = spec["token_cmd"] if (use_token_flow and spec.get("token_cmd")) else spec["login_cmd"]
    if not cmd:
        raise HTTPException(400, f"{spec['label']} has no supported login command yet")
    env = {**os.environ, **ai_providers.account_env(ROOT, pid, aid), "IS_SANDBOX": "1"}
    # The login must not inherit ambient credentials, or the CLI may decide it is
    # already authenticated and never print a URL.
    env.pop("ANTHROPIC_API_KEY", None)
    if spec.get("token_env"):
        env.pop(spec["token_env"], None)
    res = await asyncio.to_thread(
        provider_login.FLOW.start,
        provider=pid, account_id=aid, cmd=list(cmd), env=env, cwd=str(ROOT),
    )
    return res


class CodeBody(BaseModel):
    code: str = Field(..., min_length=1, max_length=4000)


@router.post("/api/v1/providers/login/code")
async def api_login_code(body: CodeBody):
    res = provider_login.FLOW.submit(body.code)
    return res


@router.post("/api/v1/providers/login/cancel")
async def api_login_cancel():
    return provider_login.FLOW.cancel()


class TokenBody(BaseModel):
    token: str = Field(..., min_length=8, max_length=4000)


@router.post("/api/v1/providers/{pid}/accounts/{aid}/token")
async def api_save_token(pid: str, aid: str, body: TokenBody):
    """Store an API key / session token for one account.

    Per account rather than one global value: the key is injected as the
    provider's env var per invocation, which is the only auth path that isolates
    accounts for a CLI with no config-dir override (Gemini). Quotes are stripped —
    a pasted `"AIza…"` used to be persisted verbatim and then rejected.
    """
    spec = _known(pid)
    _known_account(pid, aid)
    tok = body.token.strip().strip("'\"").strip()
    if pid == "claude" and tok.startswith("sk-ant-api") and "oat" not in tok:
        raise HTTPException(400, "That is an API key, not a session token. Run `claude setup-token`.")
    try:
        ai_providers.save_token(ROOT, pid, aid, tok)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # A new key can see a different model list — never serve the old one.
    ai_providers.forget_models(pid)
    return {"ok": True, "env": spec["token_env"],
            "providers": await asyncio.to_thread(ai_providers.all_status, ROOT)}


@router.delete("/api/v1/providers/{pid}/accounts/{aid}/token")
async def api_clear_token(pid: str, aid: str):
    _known_account(pid, aid)
    removed = ai_providers.clear_token(ROOT, pid, aid)
    ai_providers.forget_models(pid)
    return {"ok": True, "removed": removed,
            "providers": await asyncio.to_thread(ai_providers.all_status, ROOT)}
