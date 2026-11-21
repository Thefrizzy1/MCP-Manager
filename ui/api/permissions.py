"""Permissions surface: what the automation may do, and the approvals it waits on.

Deliberately dashboard-only for writes — see core/permissions.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import permissions
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])


@router.get("/api/v1/permissions")
async def api_v1_permissions():
    return permissions.overview(ROOT)


class SetBody(BaseModel):
    action: str = Field(..., max_length=80)
    value: str = Field(..., max_length=10)


@router.post("/api/v1/permissions/set")
async def api_v1_permissions_set(body: SetBody):
    try:
        permissions.set_value(ROOT, body.action, body.value)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return permissions.overview(ROOT)


class DecideBody(BaseModel):
    id: int
    approve: bool


@router.post("/api/v1/permissions/decide")
async def api_v1_permissions_decide(body: DecideBody):
    try:
        permissions.decide(ROOT, body.id, body.approve)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return permissions.overview(ROOT)
