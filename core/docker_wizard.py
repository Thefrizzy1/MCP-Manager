"""Map running Docker containers (published ports) to Plutus service URL env keys."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from typing import Any

import httpx

from config import cfg

# Container private port -> (service_id, env key for base URL)
_INNER_PORT: dict[int, tuple[str, str]] = {
    8096: ("jellyfin", "JELLYFIN_URL"),
    8989: ("sonarr", "SONARR_URL"),
    7878: ("radarr", "RADARR_URL"),
    8686: ("lidarr", "LIDARR_URL"),
    5055: ("jellyseerr", "JELLYSEERR_URL"),
    8080: ("qbittorrent", "QBITTORRENT_URL"),
    2283: ("immich", "IMMICH_URL"),
    8123: ("homeassistant", "HA_URL"),
    3030: ("habitica", "HABITICA_URL"),
    5678: ("n8n", "N8N_URL"),
    5050: ("ntfy", "NTFY_URL"),
    8384: ("syncthing", "SYNCTHING_URL"),
    3001: ("uptime_kuma", "UPTIME_KUMA_URL"),
    8188: ("comfyui", "COMFYUI_URL"),
}

# When port alone is ambiguous, image/container name substring (lowercase)
_IMAGE_HINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("jellyfin", "JELLYFIN_URL", ("jellyfin",)),
    ("sonarr", "SONARR_URL", ("sonarr",)),
    ("radarr", "RADARR_URL", ("radarr",)),
    ("lidarr", "LIDARR_URL", ("lidarr",)),
    ("jellyseerr", "JELLYSEERR_URL", ("jellyseerr", "overseerr")),
    ("qbittorrent", "QBITTORRENT_URL", ("qbittorrent", "qbit")),
    ("immich", "IMMICH_URL", ("immich",)),
    ("homeassistant", "HA_URL", ("homeassistant", "home-assistant")),
    ("habitica", "HABITICA_URL", ("habitica",)),
    ("n8n", "N8N_URL", ("n8n",)),
    ("ntfy", "NTFY_URL", ("ntfy",)),
    ("syncthing", "SYNCTHING_URL", ("syncthing",)),
    ("uptime_kuma", "UPTIME_KUMA_URL", ("uptime-kuma", "uptime_kuma", "kuma")),
    ("comfyui", "COMFYUI_URL", ("comfyui", "comfy")),
    ("nextcloud", "NEXTCLOUD_URL", ("nextcloud",)),
    ("obsidian", "OBSIDIAN_URL", ("obsidian",)),
)


async def _containers_via_socket() -> list[dict] | None:
    sock = cfg.docker_socket
    if not sock:
        return None
    try:
        transport = httpx.AsyncHTTPTransport(uds=sock)
        async with httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(20.0), base_url="http://docker"
        ) as client:
            r = await client.get("/containers/json?all=false")
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


def _containers_via_cli() -> list[dict] | None:
    """Parse `docker ps --format json` (one JSON object per line)."""
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            return None
        out: list[dict] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _parse_cli_ports(ports_field: str) -> list[tuple[int | None, int]]:
    """From docker ps Ports string, extract (public_port, private_port). Public may be None."""
    pairs: list[tuple[int | None, int]] = []
    if not ports_field or ports_field.strip() == "":
        return pairs
    for m in re.finditer(r":(\d+)>(\d+)/(tcp|udp)", ports_field.replace(" ", "")):
        pairs.append((int(m.group(1)), int(m.group(2))))
    return pairs


def _match_service(pub: int | None, priv: int, blob: str) -> tuple[str | None, str | None]:
    if priv in _INNER_PORT:
        sid, ek = _INNER_PORT[priv]
        return sid, ek
    if pub is not None and pub in _INNER_PORT:
        sid, ek = _INNER_PORT[pub]
        return sid, ek
    for sid, ek, needles in _IMAGE_HINTS:
        if any(n in blob for n in needles):
            if ek == "QBITTORRENT_URL" and priv != 8080:
                continue
            return sid, ek
    return None, None


def _suggestions_from_docker_payload(containers: list[dict], lan_host: str) -> tuple[list[dict], str | None]:
    """Build suggestion rows from Docker socket JSON or CLI json lines."""
    host = lan_host.strip() or cfg.mcp_lan_host.strip() or "127.0.0.1"
    seen_service: set[str] = set()
    rows: list[dict] = []
    err_hint: str | None = None

    for c in containers:
        name = "? "
        image = ""
        port_pairs: list[tuple[int | None, int]] = []

        if "Names" in c and "Image" in c:
            names = c.get("Names") or []
            name = names[0].lstrip("/") if names else ""
            image = (c.get("Image") or "").lower()
            for p in c.get("Ports") or []:
                pub = p.get("PublicPort")
                priv = p.get("PrivatePort")
                if priv is not None:
                    port_pairs.append((pub if pub else None, int(priv)))
        else:
            name = str(c.get("Names", "") or c.get("name", "")).strip().lstrip("/")
            image = str(c.get("Image", "") or "").lower()
            pf = str(c.get("Ports", "") or "")
            port_pairs = _parse_cli_ports(pf)

        blob = f"{image} {name}".lower()

        for pub, priv in port_pairs:
            sid, ek = _match_service(pub, priv, blob)
            if not sid or not ek:
                continue
            if pub is None:
                continue
            if sid in seen_service:
                continue
            url = f"http://{host}:{pub}".rstrip("/")
            rows.append(
                {
                    "service_id": sid,
                    "container": name,
                    "image": image[:80],
                    "public_port": pub,
                    "container_port": priv,
                    "env": {ek: url},
                    "source": "docker",
                    "editable_keys": [
                        {"key": ek, "label": ek.replace("_", " ").title(), "value": url}
                    ],
                }
            )
            seen_service.add(sid)

    if not rows and not containers:
        err_hint = "No containers returned (is Docker running? On Windows use Docker Desktop and try from the same machine.)"
    return rows, err_hint


async def discover_docker_suggestions(lan_host: str) -> dict[str, Any]:
    raw = await _containers_via_socket()
    source = "socket"
    if raw is None:
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _containers_via_cli)
        source = "cli"
    if raw is None:
        return {
            "ok": False,
            "source": None,
            "suggestions": [],
            "error": "Could not reach Docker (socket failed and docker CLI unavailable or errored).",
        }

    rows, hint = _suggestions_from_docker_payload(raw, lan_host)
    return {
        "ok": True,
        "source": source,
        "lan_host": (lan_host.strip() or cfg.mcp_lan_host).strip(),
        "suggestions": rows,
        "hint": hint,
        "containers_seen": len(raw),
    }
