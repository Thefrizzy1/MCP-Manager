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


@router.post("/api/v1/providers/{pid}/accounts/{aid}/test")
async def api_test_account(pid: str, aid: str, with_mcp: bool = False):
    """Real capability test: executes a prompt through the CLI, optionally with
    Plutus's own MCP config attached. Not an HTTP ping."""
    _known_account(pid, aid)
    mcp_path = None
    if with_mcp:
        from ui.runtime import _agent_mcp_target
        url, token = _agent_mcp_target()
        mcp_path = agent_runner.write_plutus_mcp_config(ROOT, mcp_url=url, token=token)
    res = await asyncio.to_thread(
        ai_providers.capability_test, ROOT, pid, aid, mcp_config_path=mcp_path
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
    """Paste-a-token fallback, stored per account rather than globally.

    Quotes are stripped: a pasted `"sk-ant-oat01-…"` used to be persisted verbatim
    and then rejected as an invalid bearer token, with nothing explaining why.
    """
    spec = _known(pid)
    _known_account(pid, aid)
    if not spec.get("token_env"):
        raise HTTPException(400, f"{spec['label']} does not support token auth")
    tok = body.token.strip().strip("'\"").strip()
    if tok.startswith("sk-ant-api") and "oat" not in tok:
        raise HTTPException(400, "That is an API key, not a session token. Run `claude setup-token`.")
    d = ai_providers.account_dir(ROOT, pid, aid)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "plutus_token"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tok)
    return {"ok": True, "providers": await asyncio.to_thread(ai_providers.all_status, ROOT)}
