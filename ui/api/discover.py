"""Discovery surface: single-host probe, docker-aware wizard scan, OpenAPI introspection."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.discover_services import probe_host
from core.wizard_scan import build_wizard_scan
from ui.api.deps import verify_auth
from ui.runtime import _services_live

router = APIRouter(dependencies=[Depends(verify_auth)])


class OpenApiIntrospectBody(BaseModel):
    url: str = Field(..., min_length=3, max_length=2000)


@router.post("/api/v1/openapi/introspect")
async def api_v1_openapi_introspect(body: OpenApiIntrospectBody):
    """Discover a service's OpenAPI/Swagger spec and list its endpoints."""
    from core.openapi_discover import introspect
    return await introspect(body.url)


class DiscoverBody(BaseModel):
    host: str = Field(..., min_length=1, max_length=253)


@router.post("/api/v1/discover")
async def api_v1_discover(body: DiscoverBody):
    """Probe common service ports on a single host (wizard)."""
    hits = await probe_host(body.host)
    return {"host": body.host.strip(), "hits": hits}


class WizardScanBody(BaseModel):
    host: str = Field(..., min_length=1, max_length=253)
    include_port_scan: bool = True


@router.post("/api/v1/wizard/scan")
async def api_v1_wizard_scan(body: WizardScanBody):
    """Docker-aware URL suggestions (published ports) plus optional LAN port probes."""
    return await build_wizard_scan(
        body.host,
        include_port_scan=body.include_port_scan,
        services=_services_live(),
    )
