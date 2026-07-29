"""Real LAN discovery for the setup wizard.

A service is only ever reported if a TCP connection to host:port actually
succeeds *and* the response looks like that service (HTTP fingerprint, an
auth challenge, or an SSH banner). Known ports alone never imply a service —
that produced false positives where every homelab port was assumed present.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

# (service_id, port, method, path, fingerprint substring or None)
# The fingerprint is checked against the response body on a 2xx/3xx. A 401/403
# is treated as "auth required — almost certainly the real service". Anything
# 5xx or a body that fails the fingerprint is rejected.
HTTP_PROBES: tuple[tuple[str, int, str, str, str | None], ...] = (
    ("jellyfin", 8096, "GET", "/System/Info/Public", "jellyfin"),
    ("sonarr", 8989, "GET", "/api/v3/system/status", None),        # 401 without key
    ("radarr", 7878, "GET", "/api/v3/system/status", None),
    ("lidarr", 8686, "GET", "/api/v3/system/status", None),
    ("jellyseerr", 5055, "GET", "/api/v1/status", "version"),
    ("qbittorrent", 8080, "GET", "/api/v2/app/version", None),     # 403 without auth
    ("immich", 2283, "GET", "/api/server/ping", "pong"),
    ("homeassistant", 8123, "GET", "/manifest.json", "home"),
    ("n8n", 5678, "GET", "/healthz", None),
    ("syncthing", 8384, "GET", "/rest/noauth/health", None),
    ("uptime_kuma", 3001, "GET", "/", "uptime"),
    ("comfyui", 8188, "GET", "/system_stats", "system"),
    ("ntfy", 5050, "GET", "/v1/health", "healthy"),
    ("nextcloud", 80, "GET", "/status.php", "installed"),
)


async def tcp_open(host: str, port: int, *, timeout: float = 1.5) -> bool:
    """True if a TCP connection to host:port completes within the timeout."""
    try:
        fut = asyncio.open_connection(host, port)
        _reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _ssh_banner(host: str, *, port: int = 22, timeout: float = 1.5) -> bool:
    """True if port 22 speaks SSH (banner starts with 'SSH-')."""
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        try:
            data = await asyncio.wait_for(reader.read(64), timeout=timeout)
        finally:
            writer.close()
        return data[:4] == b"SSH-"
    except Exception:
        return False


async def _http_probe(
    host: str, svc_id: str, port: int, method: str, path: str, needle: str | None, timeout: float
) -> dict[str, Any] | None:
    base = f"http://{host}:{port}"
    # 1) TCP gate — a closed port is never the service.
    if not await tcp_open(host, port, timeout=min(timeout, 1.5)):
        return None
    # 2) The port is open; confirm it actually behaves like the service.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), verify=False) as client:
            r = await client.request(method, base + path)
    except Exception as e:
        log.debug("HTTP probe failed for %s: %s", base + path, e)
        return None
    sc = r.status_code
    if sc in (401, 403):
        return {"service_id": svc_id, "port": port, "suggested_url": base,
                "status": f"HTTP {sc} (auth required — likely {svc_id})", "reachable": True}
    if 200 <= sc < 400:
        if needle and needle.lower() not in r.text.lower():
            return None  # open port, wrong service — reject
        return {"service_id": svc_id, "port": port, "suggested_url": base,
                "status": f"HTTP {sc}", "reachable": True}
    return None  # 4xx (non-auth) / 5xx → not a confirmed match


async def probe_host(host: str, *, timeout: float = 2.5) -> list[dict[str, Any]]:
    """Probe known homelab ports on `host`. Returns only services that actually
    respond — never bare port assumptions."""
    host = host.strip().rstrip(".")
    if not host:
        return []

    tasks = [_http_probe(host, *p, timeout) for p in HTTP_PROBES]
    results = [r for r in await asyncio.gather(*tasks) if r]

    if await _ssh_banner(host):
        results.append({"service_id": "ssh", "port": 22, "suggested_url": "",
                        "status": "SSH banner detected", "reachable": True})

    results.sort(key=lambda x: (x["service_id"], x["port"]))
    return results
