"""Tool-exposure surface: gate read, section toggles, slicer preview, active intent.

(Workstream B replaces the slicer/gate with per-profile MCP mounts; until then
these endpoints stay a pure move of the existing behaviour.)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.tool_gate import (
    build_tool_slice,
    load_gate,
    set_active_intent,
    set_section_disabled,
    set_tool_enabled,
)
from ui.api.deps import verify_auth
from ui.runtime import ROOT, capabilities

router = APIRouter(dependencies=[Depends(verify_auth)])


class ToolGateToggleBody(BaseModel):
    tool: str = Field(..., min_length=1, max_length=160)
    enabled: bool = True


class ToolGateSectionBody(BaseModel):
    section: str = Field(..., min_length=3, max_length=32)
    disabled: bool = True


class IntentBody(BaseModel):
    intent: str = ""


@router.get("/api/v1/tools/gate")
async def api_v1_tools_gate_get():
    return load_gate(ROOT)


@router.get("/api/v1/tools/slicer")
async def api_v1_tools_slicer(intent: str = ""):
    return build_tool_slice(ROOT, intent=intent)


@router.post("/api/v1/tools/gate/tool")
async def api_v1_tools_gate_tool(body: ToolGateToggleBody):
    set_tool_enabled(ROOT, body.tool, body.enabled)
    capabilities.invalidate()
    return {"ok": True}


@router.post("/api/v1/tools/gate/section")
async def api_v1_tools_gate_section(body: ToolGateSectionBody):
    try:
        set_section_disabled(ROOT, body.section, body.disabled)
    except ValueError as e:
        raise HTTPException(400, str(e))
    capabilities.invalidate()
    return {"ok": True}


@router.get("/api/v1/tools/intent")
async def api_v1_tools_intent_get():
    """Read the active server-wide tool-slicer intent."""
    g = load_gate(ROOT)
    cur = g.get("active_intent", "")
    return {"active_intent": cur, "slice": build_tool_slice(ROOT, cur)}


@router.post("/api/v1/tools/intent")
async def api_v1_tools_intent_set(body: IntentBody):
    """Persist the active intent. MCP clients must reconnect to see the new manifest."""
    normalized = set_active_intent(ROOT, body.intent)
    capabilities.invalidate()
    return {"ok": True, "active_intent": normalized, "slice": build_tool_slice(ROOT, normalized)}
