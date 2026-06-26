"""Canonical service registry for dashboard rows and router capability ownership."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.builtin_services import SERVICES
from core.custom_integrations import custom_integrations_as_services


def builtin_services() -> list[dict[str, Any]]:
    return list(SERVICES)


def all_services(root: Path) -> list[dict[str, Any]]:
    from tools.public_apis_bulk import PUBLIC_SERVICES_DASHBOARD

    custom = custom_integrations_as_services(root)
    rows = builtin_services() + list(PUBLIC_SERVICES_DASHBOARD) + custom
    return [svc for svc in rows if svc.get("tools") or svc.get("section") == "custom"]


def service_tool_map(root: Path, services: list[dict[str, Any]] | None = None) -> dict[str, str]:
    rows = services if services is not None else all_services(root)
    out: dict[str, str] = {}
    for svc in rows:
        sid = str(svc.get("id") or "")
        for tool in svc.get("tools") or []:
            name = str(tool.get("name") or "")
            if name and sid:
                out[name] = sid
    return out
