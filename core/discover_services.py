"""Lightweight LAN probes for setup wizard (common homelab ports)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

# (service_id, port, method, path, expect_substring_or_none)
PROBES: tuple[tuple[str, int, str, str, str | None], ...] = (
    ("jellyfin", 8096, "GET", "/health", None),
    ("sonarr", 8989, "GET", "/api/v3/system/status", None),
    ("radarr", 7878, "GET", "/api/v3/system/status", None),
    ("lidarr", 8686, "GET", "/api/v3/system/status", None),
    ("jellyseerr", 5055, "GET", "/", None),
    ("qbittorrent", 8080, "GET", "/api/v2/app/version", None),
    ("immich", 2283, "GET", "/api/server/ping", None),
    ("homeassistant", 8123, "GET", "/", None),
    ("n8n", 5678, "GET", "/healthz", None),
    ("syncthing", 8384, "GET", "/", None),
    ("uptime_kuma", 3001, "GET", "/", None),
    ("comfyui", 8188, "GET", "/system_stats", None),
    ("ntfy", 5050, "GET", "/v1/health", None),
)


async def probe_host(host: str, *, timeout: float = 2.5) -> list[dict[str, Any]]:
    """Try HTTP hits against host:port for known stacks. Returns suggested base URLs."""
    host = host.strip().rstrip(".")
    if not host:
        return []

    results: list[dict[str, Any]] = []

    async def one(svc_id: str, port: int, method: str, path: str, needle: str | None) -> None:
        base = f"http://{host}:{port}"
        url = base + path
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), verify=False) as client:
                r = await client.request(method, url)
            ok = r.status_code < 600
            hint = f"HTTP {r.status_code}"
            body_ok = True
            if needle and needle not in r.text:
                body_ok = False
            if ok and (needle is None or body_ok):
                results.append(
                    {
                        "service_id": svc_id,
                        "port": port,
                        "suggested_url": base,
                        "status": hint,
                        "reachable": True,
                    }
                )
            elif r.status_code in (401, 403):
                results.append(
                    {
                        "service_id": svc_id,
                        "port": port,
                        "suggested_url": base,
                        "status": f"{hint} (auth required — likely correct service)",
                        "reachable": True,
                    }
                )
        except Exception as e:
            log.debug("Probe failed for %s: %s", url, e)

    await asyncio.gather(*[one(*p) for p in PROBES])
    results.sort(key=lambda x: (x["service_id"], x["port"]))
    return results
