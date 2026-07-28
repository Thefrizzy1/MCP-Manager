"""Connection surface: probe a service, try its tools, edit config, ignore/restore."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import cfg
from core.dashboard_health import probe_service_row, service_state_from_row
from core.smoke_service_tools import run_service_smoke_tools
from ui.api.deps import verify_auth
from ui import runtime
from ui.runtime import ROOT, _services_live, tools

router = APIRouter(dependencies=[Depends(verify_auth)])


@router.get("/service/test/{sid}")
async def test_service(sid: str):
    svc = next((s for s in _services_live() if s["id"] == sid), None)
    if not svc:
        return JSONResponse({"ok": False, "error": "Unknown service"})
    row = await probe_service_row(svc, cfg)
    # Persist this fresh probe into the shared health caches so the Connections
    # table reflects the new state immediately (not just on a full Test-all).
    state = service_state_from_row(row)
    async with runtime._health_lock:
        runtime._health_cache[sid] = row.get("ok")
        runtime._health_states[sid] = state
    o = row.get("ok")
    detail = (row.get("detail") or "").strip()
    summary = (row.get("summary") or "").strip()
    lines = [summary] if summary else []
    if detail:
        lines.extend(["", detail])
    output = "\n".join(lines).strip()
    if o is True:
        tri = "ok"
    elif o is False:
        tri = "fail"
    elif row.get("kind") == "unconfigured":
        tri = "uncfg"
    else:
        tri = "unknown"
    return {
        "ok": o is True,
        "output": output or summary or "(no output)",
        "summary": summary,
        "detail": detail,
        "kind": row.get("kind"),
        "state": state,
        "status_code": row.get("status_code"),
        "tri": tri,
        "error": None,
    }


@router.post("/service/smoke-tools/{sid}")
async def service_smoke_tools(sid: str):
    """Run each integration tool with server-side hardcoded payloads (safe/smoke only)."""
    svc = next((s for s in _services_live() if s["id"] == sid), None)
    if not svc:
        return JSONResponse({"error": "Unknown service"}, status_code=404)
    tool_entries = svc.get("tools", [])
    rep = await run_service_smoke_tools(tools.raw_manager, tool_entries)
    return rep


@router.get("/api/v1/service/{sid}/config")
async def api_v1_service_config(sid: str):
    """Editable env fields for a service's inline Configure form (secrets masked)."""
    from core.env_store import read_env
    from core.service_utils import service_config_fields, service_url
    svc = next((s for s in _services_live() if s["id"] == sid), None)
    if not svc:
        raise HTTPException(404, "Unknown service")
    env = read_env()
    # Services whose real config is structured (not flat env keys) get a
    # dedicated manager UI: SSH hosts, SMB shares + allowed filesystem paths.
    manager = {"ssh": "ssh", "filesystem": "filesystem"}.get(sid, "")
    return {
        "id": sid,
        "label": svc["label"],
        "icon": svc.get("icon", "🔌"),
        "url": service_url(svc, env),
        "fields": service_config_fields(svc, env),
        "manager": manager,
        "documentation_url": svc.get("documentation_url", ""),
    }


class ServiceIgnoreBody(BaseModel):
    ignored: bool = True


@router.post("/api/v1/service/{sid}/ignore")
async def api_v1_service_ignore(sid: str, body: ServiceIgnoreBody):
    """Hide/show a connection: ignored services grey out and drop out of stats."""
    from core.ui_prefs import set_service_ignored
    svc = next((s for s in _services_live() if s["id"] == sid), None)
    if not svc:
        raise HTTPException(404, "Unknown service")
    return {"ok": True, "ignored": set_service_ignored(ROOT, sid, body.ignored)}
