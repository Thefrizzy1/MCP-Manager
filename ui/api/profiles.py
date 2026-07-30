"""Profile surface: named tool subsets, each served at /mcp/p/<name>.

Replaces the deleted tool-gate/slicer endpoints. Profiles are restart-to-apply
for the MCP server (a separate process) — the response says so.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import profiles as profiles_mod
from core import tool_exposure
from ui.api.deps import verify_auth
from ui.runtime import ROOT, all_tool_names, tools

router = APIRouter(dependencies=[Depends(verify_auth)])

_RESTART_NOTE = "New or changed profiles take effect after the MCP server restarts."


class ProfileBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    label: str = ""
    intent: str = ""
    sections: list[str] = Field(default_factory=list)
    include_tools: list[str] = Field(default_factory=list)
    exclude_tools: list[str] = Field(default_factory=list)


def _view(prof: dict, names: list[str]) -> dict:
    allow = profiles_mod.resolve_tool_names(prof, names)
    return {**prof, "endpoint_path": f"/mcp/p/{prof['name']}", "tool_count": len(allow)}


@router.get("/api/v1/profiles")
async def list_profiles():
    names = all_tool_names()
    profs = profiles_mod.load_profiles(ROOT)
    return {
        "profiles": [_view(p, names) for p in profs],
        "categories": sorted(profiles_mod.TOOL_CATEGORIES.keys()),
        "presets": {k: list(v) for k, v in profiles_mod.INTENT_PRESETS.items()},
        "total_tools": len(names),
        "restart_note": _RESTART_NOTE,
    }


@router.get("/api/v1/profiles/preview")
async def preview_profile(intent: str = "", sections: str = ""):
    """Resolve an in-progress profile so the builder can show a live tool count."""
    names = all_tool_names()
    prof = {
        "name": "preview",
        "intent": intent,
        "sections": [s for s in sections.replace(",", " ").split() if s],
        "include_tools": [],
        "exclude_tools": [],
    }
    allow = sorted(profiles_mod.resolve_tool_names(prof, names))
    return {"tool_count": len(allow), "tools": allow, "slice": profiles_mod.build_tool_slice(names, intent)}


@router.post("/api/v1/profiles")
async def create_profile(body: ProfileBody):
    profs = profiles_mod.load_profiles(ROOT)
    if any(p["name"] == body.name.strip().lower() for p in profs):
        raise HTTPException(400, f"A profile named '{body.name}' already exists.")
    new = body.model_dump()
    new["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        saved = profiles_mod.save_profiles(ROOT, profs + [new])
    except ValueError as e:
        raise HTTPException(400, str(e))
    names = all_tool_names()
    return {"ok": True, "restart_note": _RESTART_NOTE, "profiles": [_view(p, names) for p in saved]}


@router.post("/api/v1/profiles/{name}")
async def update_profile(name: str, body: ProfileBody):
    profs = profiles_mod.load_profiles(ROOT)
    idx = next((i for i, p in enumerate(profs) if p["name"] == name.strip().lower()), None)
    if idx is None:
        raise HTTPException(404, "profile not found")
    updated = body.model_dump()
    updated["name"] = name  # the URL-stable name comes from the path, not the body
    updated["created"] = profs[idx].get("created") or ""
    profs[idx] = updated
    try:
        saved = profiles_mod.save_profiles(ROOT, profs)
    except ValueError as e:
        raise HTTPException(400, str(e))
    names = all_tool_names()
    return {"ok": True, "restart_note": _RESTART_NOTE, "profiles": [_view(p, names) for p in saved]}


@router.delete("/api/v1/profiles/{name}")
async def delete_profile(name: str):
    profs = profiles_mod.load_profiles(ROOT)
    kept = [p for p in profs if p["name"] != name.strip().lower()]
    if len(kept) == len(profs):
        raise HTTPException(404, "profile not found")
    saved = profiles_mod.save_profiles(ROOT, kept)
    names = all_tool_names()
    return {"ok": True, "restart_note": _RESTART_NOTE, "profiles": [_view(p, names) for p in saved]}


@router.get("/api/v1/tools/slicer")
async def tools_slicer(intent: str = ""):
    """Read-only category/intent preview (there is no global gate to apply)."""
    return profiles_mod.build_tool_slice(all_tool_names(), intent)


# ── Global tool exposure (the token-optimisation "slicer") ────────────────────

class ExposureBody(BaseModel):
    disabled_categories: list[str] = Field(default_factory=list)


@router.get("/api/v1/tools/exposure")
async def get_exposure():
    return tool_exposure.exposure_report(ROOT, tools.raw_manager)


@router.post("/api/v1/tools/exposure")
async def set_exposure(body: ExposureBody):
    # Only the categories — per-tool switches are edited elsewhere and must not be
    # cleared by a save from this screen.
    tool_exposure.save_exposure(ROOT, body.disabled_categories)
    return {"ok": True, **tool_exposure.exposure_report(ROOT, tools.raw_manager)}


# ── Public APIs: one card, individual switches ────────────────────────────────

class PublicApisBody(BaseModel):
    disabled_tools: list[str] = Field(default_factory=list)


@router.get("/api/v1/public-apis")
async def get_public_apis():
    """The ~58 free public APIs grouped for display, with their on/off state."""
    from tools.public_apis_bulk import public_api_groups, public_tool_names

    disabled = set(tool_exposure.load_exposure(ROOT)["disabled_tools"])
    known = set(public_tool_names())
    labels = {t["name"]: t["label"] for t in _public_tool_rows()}
    return {
        "groups": [
            {
                **g,
                "tools": [
                    {"name": n, "label": labels.get(n, n), "enabled": n not in disabled}
                    for n in g["tools"]
                ],
            }
            for g in public_api_groups()
        ],
        "total": len(known),
        "disabled": sorted(disabled & known),
        "restart_note": "Changes apply after the MCP server restarts.",
    }


def _public_tool_rows() -> list[dict]:
    from tools.public_apis_bulk import PUBLIC_SERVICES_DASHBOARD
    return PUBLIC_SERVICES_DASHBOARD[0]["tools"]


@router.post("/api/v1/public-apis")
async def set_public_apis(body: PublicApisBody):
    """Switch individual public APIs off. A disabled tool is not registered on the
    served /mcp instance at all, so this shrinks the manifest rather than only
    hiding a tile."""
    from tools.public_apis_bulk import public_tool_names

    known = set(public_tool_names())
    unknown = [t for t in body.disabled_tools if t not in known]
    if unknown:
        raise HTTPException(400, f"not public API tools: {', '.join(sorted(unknown)[:5])}")

    # Preserve per-tool switches for anything outside this card's scope.
    keep = [t for t in tool_exposure.load_exposure(ROOT)["disabled_tools"] if t not in known]
    saved = tool_exposure.save_exposure(ROOT, disabled_tools=keep + list(body.disabled_tools))
    return {"ok": True, "disabled": sorted(set(saved["disabled_tools"]) & known),
            "restart_note": "Changes apply after the MCP server restarts."}
