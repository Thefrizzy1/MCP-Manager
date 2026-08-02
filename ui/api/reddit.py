"""Reddit accounts: several logins rather than one set of REDDIT_* env vars.

Secrets never travel back out — every response goes through
``reddit_accounts.public_accounts``, which returns labels and usernames only. The
same reasoning as the AI provider surface: the UI needs to know *which* accounts
exist, never what they are authenticated with.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import reddit_accounts as ra
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])


def _payload() -> dict:
    return {"accounts": ra.public_accounts(ROOT), "default": ra.default_id(ROOT)}


@router.get("/api/v1/reddit/accounts")
async def api_reddit_accounts():
    return _payload()


class AccountBody(BaseModel):
    label: str = Field(default="", max_length=60)
    client_id: str = Field(..., min_length=1, max_length=200)
    client_secret: str = Field(..., min_length=1, max_length=200)
    username: str = Field(..., min_length=1, max_length=60)
    password: str = Field(..., min_length=1, max_length=200)


@router.post("/api/v1/reddit/accounts")
async def api_add_reddit_account(body: AccountBody):
    try:
        ra.add_account(ROOT, body.label, client_id=body.client_id,
                       client_secret=body.client_secret, username=body.username,
                       password=body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _forget(None)
    return {"ok": True, **_payload()}


class AccountPatch(BaseModel):
    label: str | None = Field(default=None, max_length=60)
    client_id: str | None = Field(default=None, max_length=200)
    client_secret: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=60)
    password: str | None = Field(default=None, max_length=200)


@router.post("/api/v1/reddit/accounts/{account_id}")
async def api_update_reddit_account(account_id: str, body: AccountPatch):
    try:
        ra.update_account(ROOT, account_id, body.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, "account not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    _forget(account_id)
    return {"ok": True, **_payload()}


@router.delete("/api/v1/reddit/accounts/{account_id}")
async def api_remove_reddit_account(account_id: str):
    try:
        removed = ra.remove_account(ROOT, account_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not removed:
        raise HTTPException(404, "account not found")
    _forget(account_id)
    return {"ok": True, **_payload()}


@router.post("/api/v1/reddit/accounts/{account_id}/default")
async def api_set_default_reddit_account(account_id: str):
    try:
        ra.set_default(ROOT, account_id)
    except KeyError:
        raise HTTPException(404, "account not found")
    return {"ok": True, **_payload()}


def _forget(account_id: str | None) -> None:
    """Drop the cached OAuth token so changed credentials take effect now.

    Without this, editing a password leaves the old token valid for up to an
    hour and the account keeps answering as though nothing changed — the kind of
    silent staleness that looks like the edit was ignored.
    """
    try:
        from tools.social import forget_reddit_token
        forget_reddit_token(account_id or "")
    except Exception:
        pass          # the cache is an optimisation, never a correctness gate
