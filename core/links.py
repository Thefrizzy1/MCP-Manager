"""Build client-facing MCP / UI URLs without hardcoding LAN IPs."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Request

from config import cfg


def client_facing_links(request: Request) -> dict[str, str]:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if not host:
        host = f"127.0.0.1:{cfg.ui_port}"

    base_ui = f"{proto}://{host}".rstrip("/")

    if cfg.public_mcp_base.strip():
        mcp_base = cfg.public_mcp_base.rstrip("/")
        mcp_url = f"{mcp_base}/mcp"
    else:
        parsed = urlparse(f"//{host}")
        hostname = parsed.hostname or host.split(":")[0]
        if hostname and host.count(":") >= 1 and "]" not in host:
            parts = host.rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                hostname = parts[0]
        mcp_url = f"{proto}://{hostname}:{cfg.mcp_port}/mcp"

    return {
        "ui_base": base_ui,
        "ui_path": f"{base_ui}/ui",
        "mcp_url": mcp_url,
        "mcp_port": str(cfg.mcp_port),
        "ui_port": str(cfg.ui_port),
    }
