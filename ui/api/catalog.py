"""Catalog surface: custom-integration cards (data/custom_integrations.json)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from core.custom_integrations import load_raw, save_raw
from ui.api.deps import verify_auth
from ui import runtime
from ui.runtime import ROOT, capabilities

router = APIRouter(dependencies=[Depends(verify_auth)])


@router.get("/settings/custom-integrations")
async def settings_custom_integrations_get():
    return load_raw(ROOT)


@router.post("/settings/custom-integrations")
async def settings_custom_integrations_post(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(400, "Expected a JSON object at the root")
        save_raw(ROOT, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except json.JSONDecodeError:  # invalid JSON body from client
        raise HTTPException(400, "Body must be valid JSON")
    async with runtime._health_lock:
        runtime._health_cache, runtime._health_ts = {}, 0.0
    capabilities.invalidate()
    return {"ok": True}
