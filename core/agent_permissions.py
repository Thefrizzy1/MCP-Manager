"""Tool-permission levels for the headless agent.

The agent runs with --dangerously-skip-permissions and can reach Plutus's tools
while reading untrusted web pages, so a prompt-injection could try to trigger
destructive actions. This module maps a permission level to a Claude Code
`--disallowedTools` list (disallow wins over allow), giving three postures:

- ``strict_read`` — reads only; every mutating tool blocked. For pure audits.
- ``safe``  (default) — reads + note-writing (so playbooks can persist to the
  library), but infrastructure/irreversible tools blocked.
- ``all``   — nothing blocked (full access).

Classification is explicit here (not derived from the smoke-tester's exclude
list, which has a different purpose — e.g. obsidian_write_note is excluded there
but is exactly what a research playbook needs).
"""
from __future__ import annotations

LEVELS = ("strict_read", "safe", "all")
DEFAULT_LEVEL = "safe"

# Infrastructure control, deletion, sending, and costly generation — blocked in
# both ``safe`` and ``strict_read``.
DANGEROUS: frozenset[str] = frozenset({
    "docker_stop_container", "docker_restart_container", "docker_start_container",
    "qbittorrent_delete", "qbittorrent_pause", "qbittorrent_resume",
    "ssh_run", "ssh_exec", "ssh_add_host", "ssh_remove_host", "smb_add_share",
    "ha_turn_on", "ha_turn_off", "ha_call_service",
    "send_email", "ntfy_send", "n8n_trigger_webhook", "syncthing_rescan",
    "fal_generate_image", "fal_flux_pro", "comfyui_generate", "comfyui_interrupt",
    "fail2ban_unban",
    "nextcloud_delete_task", "nextcloud_delete_event", "nextcloud_delete_file",
    "habitica_delete_task", "fs_move_file",
    "radarr_add_movie", "sonarr_add_series", "jellyseerr_request",
})

# Note / content writing — allowed in ``safe`` (playbooks must persist findings),
# blocked only in ``strict_read``.
WRITE: frozenset[str] = frozenset({
    "obsidian_write_note", "obsidian_append_to_note", "obsidian_create_daily_note",
    "fs_write_file",
    "nextcloud_create_note", "nextcloud_add_task", "nextcloud_add_event",
    "nextcloud_add_contact", "nextcloud_upload_file",
    "habitica_add_todo", "habitica_add_habit", "habitica_score_task",
    "weather_remember_city",
})


def normalize_level(level: str | None, default: str = DEFAULT_LEVEL) -> str:
    return level if level in LEVELS else default


def blocked_tool_names(level: str) -> set[str]:
    level = normalize_level(level)
    if level == "all":
        return set()
    if level == "strict_read":
        return set(DANGEROUS) | set(WRITE)
    return set(DANGEROUS)  # "safe"


def build_disallowed(tool_names, level: str) -> list[str]:
    """`mcp__plutus__<tool>` disallow patterns for blocked tools that actually exist."""
    blocked = blocked_tool_names(level)
    live = set(tool_names or [])
    return sorted(f"mcp__plutus__{n}" for n in blocked if n in live)


def _tool_annotations_map(tool_manager) -> dict:
    return {t.name: t.annotations for t in tool_manager.list_tools()}


def build_disallowed_from_annotations(tool_manager, level: str) -> list[str]:
    """Derive the disallow list from tool annotations rather than a hand list.

    - ``strict_read`` blocks every tool that is not read-only.
    - ``safe`` blocks every destructive tool.

    Each still unions the curated ``DANGEROUS``/``WRITE`` sets as a safety-net
    override, so a tool the hints under-classify (ssh_run, ha_call_service, …)
    stays blocked, and the derived list is always a superset of the old one.
    Fail-safe: a missing/None annotation counts as *not* read-only.
    """
    level = normalize_level(level)
    if level == "all":
        return []
    amap = _tool_annotations_map(tool_manager)
    live = set(amap)
    if level == "strict_read":
        blocked = {n for n, a in amap.items() if not (a and a.readOnlyHint)}
        blocked |= (DANGEROUS | WRITE) & live
    else:  # safe
        blocked = {n for n, a in amap.items() if a and a.destructiveHint}
        blocked |= DANGEROUS & live
    return sorted(f"mcp__plutus__{n}" for n in blocked if n in live)
