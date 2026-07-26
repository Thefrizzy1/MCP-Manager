"""Complete the four MCP tool-annotation hints on every registered tool.

Hand-authored hints always win; this only fills the gaps. Before this, readOnlyHint
was on all 193 tools and destructiveHint on ~60, but idempotentHint/openWorldHint
were on none — so a client could not tell a read from an internet call, and the
agent ACL had to be hand-maintained. Filling the gaps at registration (via the
profiles tool_filter proxy) guarantees all four are present, which
tests/test_annotations.py enforces, and lets core.agent_permissions derive the
ACL from annotations.

Hints (per the MCP spec / rebuild brief):
- readOnlyHint    the tool changes no state anywhere
- destructiveHint it can delete, overwrite, restart, or stop something
- idempotentHint  calling twice with the same args == calling once
- openWorldHint   it reaches the public internet
"""
from __future__ import annotations

# Reaches the public internet. Everything else talks to LAN/local services
# (jellyfin, *arr, immich, home assistant, nextcloud, obsidian, docker, omv,
# ssh, smb, comfyui, n8n, syncthing, tailscale, ntfy, ...).
_INTERNET_PREFIXES = ("pub_", "fal_", "habitica_")
_INTERNET_TOOLS = {
    "web_search", "web_fetch", "google_search", "wikipedia_summary",
    "weather_current", "weather_forecast", "get_context",
    "maps_distance", "currency_convert", "currency_rates", "send_email",
}

# Verb fragments that imply a destructive change, used only to fill a missing
# destructiveHint on a non-read tool.
_DESTRUCTIVE = ("delete", "remove", "stop", "restart", "down", "interrupt", "prune", "destroy", "purge")
# Verb fragments that imply a non-idempotent create/append/emit.
_NON_IDEMPOTENT = ("add", "create", "append", "send", "generate", "run", "exec",
                   "trigger", "score", "request", "rescan", "upload", "new", "draw")

REQUIRED_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def reaches_internet(name: str) -> bool:
    n = str(name or "")
    return n in _INTERNET_TOOLS or n.startswith(_INTERNET_PREFIXES)


def _looks_destructive(name: str) -> bool:
    n = str(name or "").lower()
    return any(h in n for h in _DESTRUCTIVE)


def _looks_non_idempotent(name: str) -> bool:
    n = str(name or "").lower()
    return any(h in n for h in _NON_IDEMPOTENT)


def fill_annotations(name: str, ann: dict | None) -> dict:
    """Return a copy of ``ann`` with all four hints present. Authored hints win."""
    out = dict(ann or {})
    read_only = bool(out.get("readOnlyHint", False))
    if "openWorldHint" not in out:
        out["openWorldHint"] = reaches_internet(name)
    if "destructiveHint" not in out:
        out["destructiveHint"] = False if read_only else _looks_destructive(name)
    if "idempotentHint" not in out:
        out["idempotentHint"] = True if read_only else (not _looks_non_idempotent(name))
    out.setdefault("readOnlyHint", read_only)
    return out
