"""Structured JSON for GET /api/v1/dashboard (sections) and helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import cfg
from core.capabilities import build_capability_registry
from core.service_registry import all_services, service_tool_map
from core.service_utils import is_service_configured


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ca_cert_installed() -> bool:
    p = project_root() / "data" / "ca.pem"
    return p.exists() and p.stat().st_size > 0


def tool_to_service_map() -> dict[str, str]:
    return service_tool_map(project_root())


def mcp_urls(local_ip_hint: str = "192.168.1.111") -> dict[str, Any]:
    http_local = f"http://{local_ip_hint}:{cfg.mcp_port}/mcp"
    pub = (cfg.public_mcp_base or "").strip().rstrip("/")
    https_url = None
    tls_ready = False
    if pub:
        if pub.startswith("https://"):
            tls_ready = True
            https_url = pub + "/mcp"
        elif pub.startswith("http://"):
            https_url = pub + "/mcp"
    return {
        "http_local": http_local,
        "https_or_public": https_url,
        "public_base": pub or None,
        "tls_public_url_configured": bool(pub.startswith("https://")) if pub else False,
        "tls_ca_bundle_installed": ca_cert_installed(),
        "hint_no_https_yet": not (pub.startswith("https://") if pub else False),
    }


def tailscale_snippet() -> str:
    p = cfg.mcp_port
    return (
        "Tailscale HTTPS without managing certs — on the Plutus host:\n"
        f"  tailscale serve https / http://127.0.0.1:{p}\n"
        "Internet-facing (careful):\n"
        f"  tailscale funnel {p}\n"
        "Then set PUBLIC_MCP_BASE to the https://… URL Tailscale prints and restart Plutus."
    )


def build_dashboard_payload(
    *,
    health_cache: dict[str, Any],
    tool_names: list[str],
    recent: list[dict],
    sections: set[str],
    local_ip_hint: str,
) -> dict[str, Any]:
    root = project_root()
    slist = all_services(root)
    tmap = service_tool_map(root, slist)
    caps_payload = None
    out: dict[str, Any] = {}

    if not sections or "networking" in sections:
        nu = mcp_urls(local_ip_hint)
        out["networking"] = {
            **nu,
            "mcp_lan_host": cfg.mcp_lan_host,
            "mcp_host": cfg.mcp_host,
            "mcp_port": cfg.mcp_port,
            "ui_port": cfg.ui_port,
            "mcp_require_bearer": cfg.mcp_require_bearer,
            "mcp_bearer_token_configured": bool((cfg.mcp_bearer_token or "").strip()),
            "tailscale_serve_hint": tailscale_snippet(),
        }

    if not sections or "main" in sections:
        conf_n = sum(1 for s in slist if is_service_configured(s, cfg))
        work_n = sum(1 for s in slist if health_cache.get(s["id"]) is True)
        if caps_payload is None:
            caps_payload = build_capability_registry(root, tool_names, services=slist, service_by_tool=tmap)
        out["main"] = {
            "services_total": len(slist),
            "configured_services": conf_n,
            "working_services": work_n,
            "registered_tools": len(tool_names),
            "capabilities": len(caps_payload["capabilities"]),
            "router_mode": "mcp_router",
        }

    if not sections or "capabilities" in sections:
        if caps_payload is None:
            caps_payload = build_capability_registry(root, tool_names, services=slist, service_by_tool=tmap)
        out["capabilities"] = caps_payload["capabilities"]

    if not sections or "tools" in sections:
        out["tools"] = [
            {
                "name": name,
                "service_id": tmap.get(name),
            }
            for name in sorted(tool_names)
        ]

    if not sections or "services" in sections:
        rows = []
        for s in slist:
            sid = s["id"]
            rows.append(
                {
                    "id": sid,
                    "label": s["label"],
                    "section": s["section"],
                    "configured": is_service_configured(s, cfg),
                    "health": health_cache.get(sid),
                    "tool_count": len(s.get("tools", [])),
                    "tool_names": [t["name"] for t in s.get("tools", [])],
                }
            )
        out["services"] = rows

    if not sections or "auth" in sections:
        out["auth"] = {
            "mcp_require_bearer": cfg.mcp_require_bearer,
            "mcp_bearer_configured": bool((cfg.mcp_bearer_token or "").strip()),
            "ui_username": cfg.ui_username,
        }

    if not sections or "recent" in sections:
        out["recent_tool_runs"] = recent[-25:]

    return out
