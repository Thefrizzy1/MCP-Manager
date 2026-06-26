"""Capability registry: compact router-facing surface over raw MCP tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core.service_registry import all_services, service_tool_map
from core.tool_registry import tool_safety_level


CAPABILITY_PREFIXES: dict[str, tuple[str, ...]] = {
    "media": ("jellyfin_", "sonarr_", "radarr_", "lidarr_", "jellyseerr_", "qbittorrent_"),
    "photos": ("immich_",),
    "home": ("ha_",),
    "cloud": ("nextcloud_",),
    "automation": ("n8n_",),
    "system": ("docker_", "omv_", "fs_", "ssh_", "smb_", "tailscale_", "fail2ban_", "syncthing_"),
    "ai": ("comfyui_", "fal_"),
    "public": ("pub_", "weather_", "web_", "wikipedia_", "currency_", "maps_", "google_"),
}
_PREFIX_LOOKUP: tuple[tuple[str, str], ...] = tuple(
    (prefix, capability)
    for capability, prefixes in CAPABILITY_PREFIXES.items()
    for prefix in prefixes
)


def capability_for_tool(name: str, service_id: str | None = None) -> str:
    for prefix, capability in _PREFIX_LOOKUP:
        if name.startswith(prefix):
            return capability
    return service_id or "misc"


def build_capability_registry(
    root,
    tool_names: list[str],
    *,
    services: list[dict[str, Any]] | None = None,
    service_by_tool: dict[str, str] | None = None,
    include_tools: bool = False,
) -> dict[str, Any]:
    service_by_tool = service_by_tool if service_by_tool is not None else service_tool_map(root, services)

    caps: dict[str, dict[str, Any]] = {}
    for name in tool_names:
        service_id = service_by_tool.get(name)
        cap = capability_for_tool(name, service_id)
        row = caps.setdefault(cap, {"name": cap, "services": set(), "tool_count": 0, "safety": {"0": 0, "1": 0, "2": 0}})
        if service_id:
            row["services"].add(service_id)
        level = str(tool_safety_level(name))
        row["safety"][level] = row["safety"].get(level, 0) + 1
        row["tool_count"] += 1
        if include_tools:
            row.setdefault("tools", []).append({"name": name, "service_id": service_id, "safety_level": int(level)})

    return {
        "mode": "router",
        "capabilities": [
            {**row, "services": sorted(row["services"])}
            for row in sorted(caps.values(), key=lambda r: r["name"])
        ],
    }


class CapabilityCatalog:
    """Cached compact capability view over the current tool/service registry."""

    def __init__(self, root, tool_adapter: Any, services_fn: Callable[[], list[dict[str, Any]]]):
        self.root = Path(root)
        self.tool_adapter = tool_adapter
        self.services_fn = services_fn
        self._cache_key: tuple | None = None
        self._cache_payload: dict[str, Any] | None = None

    def tool_names(self) -> list[str]:
        return self.tool_adapter.tool_names()

    def payload(self, *, include_tools: bool = False) -> dict[str, Any]:
        tool_names = self.tool_names()
        services = self.services_fn()
        key = (tuple(tool_names), tuple(str(s.get("id")) for s in services), include_tools)
        if self._cache_key != key:
            self._cache_key = key
            self._cache_payload = build_capability_registry(
                self.root,
                tool_names,
                services=services,
                include_tools=include_tools,
            )
        return self._cache_payload or {"mode": "router", "capabilities": []}

    def invalidate(self) -> None:
        self.tool_adapter.invalidate()
        self._cache_key = None
        self._cache_payload = None
