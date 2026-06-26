"""
Tool catalogue: tags + which env keys must be present to consider a tool configured.
Missing config ⇒ NOT_CONFIGURATION in UI/tests (not counted as failures).
"""

from __future__ import annotations

import shutil

from config import cfg


def looks_like_missing_service_config(message: str) -> bool:
    """True when a tool exited early because integration / host deps are absent."""
    if not message:
        return False
    t = message.strip().lower()
    if "not configured" in t:
        return True
    if "not set up" in t:
        return True
    if "set " in t and ".env" in t:
        return True
    if "tailscale command not found" in t:
        return True
    if "fail2ban-client not accessible" in t or "fail2ban-client not found" in t:
        return True
    if "obsidian rest api not configured" in t:
        return True
    return False

# Exact overrides (subset of keys for special tools).
_TOOL_KEYS_EXACT: dict[str, tuple[str, ...]] = {
    "google_search": ("google_api_key", "google_cse_id"),
    "jellyfin_recently_added": ("jellyfin_url", "jellyfin_api_key", "jellyfin_user_id"),
    "uptime_status": ("uptime_kuma_url",),
    "comfyui_status": ("comfyui_url",),
    "comfyui_queue": ("comfyui_url",),
    "comfyui_get_models": ("comfyui_url",),
}

# Longest-prefix wins after exact match.
_TOOL_PREFIX_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nextcloud_", ("nextcloud_url", "nextcloud_username", "nextcloud_password")),
    ("sonarr_", ("sonarr_url", "sonarr_api_key")),
    ("radarr_", ("radarr_url", "radarr_api_key")),
    ("lidarr_", ("lidarr_url", "lidarr_api_key")),
    ("jellyseerr_", ("jellyseerr_url", "jellyseerr_api_key")),
    ("jellyfin_", ("jellyfin_url", "jellyfin_api_key")),
    ("qbittorrent_", ("qbittorrent_url", "qbittorrent_username", "qbittorrent_password")),
    ("immich_", ("immich_url", "immich_api_key")),
    ("ha_", ("ha_url", "ha_token")),
    ("habitica_", ("habitica_url", "habitica_user_id", "habitica_api_token")),
    ("n8n_", ("n8n_url", "n8n_api_key")),
    ("obsidian_", ("obsidian_url", "obsidian_api_key")),
    ("omv_", ("omv_url", "omv_username", "omv_password")),
    ("syncthing_", ("syncthing_url", "syncthing_api_key")),
    ("ntfy_", ("ntfy_url",)),
    ("fal_", ("fal_key",)),
    ("pub_", ()),
)


def required_keys_for_tool(name: str) -> tuple[str, ...] | None:
    if name in _TOOL_KEYS_EXACT:
        return _TOOL_KEYS_EXACT[name]
    match: tuple[str, ...] | None = None
    match_len = 0
    for prefix, keys in _TOOL_PREFIX_KEYS:
        if name.startswith(prefix) and len(prefix) > match_len:
            match = keys
            match_len = len(prefix)
    return match


def is_tool_environment_ready(name: str) -> bool:
    if name in ("tailscale_status", "tailscale_ping"):
        return shutil.which("tailscale") is not None
    keys = required_keys_for_tool(name)
    if keys is None:
        return True
    return cfg.is_configured(*keys)


# Prefill for tester / API runs when hitting "Run with defaults" without JSON.
TOOL_TEST_PAYLOAD_DEFAULTS: dict[str, dict] = {
    "web_search": {"query": "Hamburg", "limit": 3},
    "weather_remember_city": {"city": "Hamburg"},
    "wikipedia_summary": {"title": "OpenStreetMap", "lang": "en"},
    "currency_convert": {"amount": 100, "from_currency": "EUR", "to_currency": "USD"},
    "currency_rates": {"base": "EUR"},
    "google_search": {"query": "Wikipedia", "num": 3},
}

try:
    from tools.public_apis_bulk import PUBLIC_TOOL_DEFAULTS as _PUB_DEFS

    TOOL_TEST_PAYLOAD_DEFAULTS.update(_PUB_DEFS)
except ImportError:
    _PUB_DEFS = {}

# Extra defaults for UI "Test tools" smoke runs (read-only / safe probes only).
TOOL_SMOKE_DEFAULTS: dict[str, dict] = {
    "jellyfin_search": {"query": "the", "limit": 2},
    "jellyfin_recently_added": {"limit": 3},
    "sonarr_search_show": {"query": "test"},
    "radarr_search_movie": {"query": "matrix"},
    "lidarr_search_artist": {"query": "beatles"},
    "immich_search": {"query": "a"},
    "immich_search_by_metadata": {"year": 2020, "city": ""},
    "ha_search_entities": {"query": "sun"},
    "nextcloud_get_events": {"calendar": "personal", "days_ahead": 1},
    "nextcloud_get_tasks": {"list_name": "tasks"},
    "nextcloud_search_contacts": {"query": "a"},
    "nextcloud_list_files": {"path": "/"},
    "obsidian_search": {"query": "note"},
    "obsidian_list_directory": {"path": ""},
    "fs_list_directory": {"path": "/"},
    "fs_search_files": {"path": "/", "pattern": "*"},
    "fs_recent_files": {"path": "/", "days": 7},
    "maps_distance": {"origin": "Hamburg, DE", "destination": "Berlin, DE", "mode": "driving"},
    "weather_current": {"location": "Hamburg"},
    "weather_forecast": {"location": "Hamburg"},
    "web_fetch": {"url": "https://example.com"},
    "tailscale_ping": {"hostname_or_ip": "127.0.0.1"},
    "wikipedia_summary": {"title": "Docker (software)", "lang": "en"},
    "currency_convert": {"amount": 50, "from_currency": "EUR", "to_currency": "GBP"},
    "currency_rates": {"base": "USD"},
    "google_search": {"query": "Frankfurter API", "num": 3},
}
TOOL_SMOKE_DEFAULTS.update(_PUB_DEFS)

# Never run these from the dashboard smoke tester (side effects, cost, or destructive).
SMOKE_TOOL_EXCLUDE: frozenset[str] = frozenset(
    {
        "fail2ban_unban",
        "qbittorrent_pause",
        "qbittorrent_resume",
        "qbittorrent_delete",
        "docker_restart_container",
        "docker_stop_container",
        "docker_start_container",
        "ha_turn_on",
        "ha_turn_off",
        "ha_call_service",
        "syncthing_rescan",
        "comfyui_generate",
        "comfyui_interrupt",
        "fal_generate_image",
        "fal_flux_pro",
        "send_email",
        "ntfy_send",
        "nextcloud_add_event",
        "nextcloud_add_task",
        "nextcloud_delete_task",
        "nextcloud_add_contact",
        "nextcloud_create_note",
        "habitica_score_task",
        "habitica_add_habit",
        "habitica_delete_task",
        "obsidian_write_note",
        "obsidian_append_to_note",
        "obsidian_get_note",
        "docker_get_logs",
        "jellyseerr_request",
        "radarr_add_movie",
        "sonarr_add_series",
        "fs_write_file",
        "n8n_trigger_webhook",
    }
)

# 0 = read-only, 1 = reversible mutation with cleanup, 2 = destructive/manual.
TOOL_SAFETY_LEVELS: dict[str, int] = {
    "nextcloud_add_task": 1,
    "nextcloud_add_event": 1,
    "nextcloud_delete_event": 2,
    "nextcloud_delete_task": 2,
    "nextcloud_delete_file": 2,
    "qbittorrent_delete": 2,
    "docker_stop_container": 2,
    "docker_restart_container": 2,
    "docker_start_container": 2,
    "fs_write_file": 1,
    "habitica_add_todo": 1,
    "habitica_delete_task": 2,
}


def tool_safety_level(tool_name: str) -> int:
    if tool_name in TOOL_SAFETY_LEVELS:
        return TOOL_SAFETY_LEVELS[tool_name]
    if tool_name in SMOKE_TOOL_EXCLUDE:
        return 2
    return 0


def merged_smoke_payload(tool_name: str) -> dict:
    base = dict(TOOL_TEST_PAYLOAD_DEFAULTS.get(tool_name, {}))
    base.update(TOOL_SMOKE_DEFAULTS.get(tool_name, {}))
    return base


ZERO_PARAM_HEALTH_TOOLS: tuple[str, ...] = (
    "docker_list_containers",
    "docker_system_info",
    "habitica_get_stats",
    "habitica_get_tasks",
    "immich_get_memories",
    "immich_list_albums",
    "immich_list_people",
    "sonarr_list_series",
    "sonarr_queue",
    "sonarr_calendar",
    "sonarr_missing",
    "radarr_list_movies",
    "radarr_queue",
    "lidarr_list_artists",
    "lidarr_queue",
    "jellyfin_recently_added",
    "jellyseerr_requests",
    "qbittorrent_list",
    "ha_get_states",
    "nextcloud_list_calendars",
    "nextcloud_list_contacts",
    "nextcloud_get_user_info",
    "nextcloud_get_activity",
    "nextcloud_get_notes",
    "nextcloud_list_shares",
    "nextcloud_get_notifications",
    "omv_system_info",
    "omv_disk_health",
    "comfyui_status",
    "comfyui_queue",
    "get_context",
    "n8n_list_workflows",
    "n8n_get_executions",
    "uptime_status",
    "fs_list_shares",
    "syncthing_status",
    "syncthing_folders",
    "syncthing_devices",
    "tailscale_status",
    "fail2ban_status",
    "obsidian_get_daily_note",
    "weather_current",
    "weather_forecast",
    "weather_remember_city",
    "nextcloud_list_files",
    "web_search",
    "wikipedia_summary",
    "currency_convert",
    "currency_rates",
    "fal_list_models_snippet",
    "pub_bored_activity",
    "pub_cloudflare_trace",
    "pub_shibe_image",
    "pub_blockchain_btc_ticker",
    "pub_official_joke",
    "pub_ipwho",
)


def merged_tool_payload(tool_name: str, user_payload: dict | None) -> dict:
    base = dict(TOOL_TEST_PAYLOAD_DEFAULTS.get(tool_name, {}))
    if user_payload:
        base.update(user_payload)
    return base


def tool_catalog_entries() -> list[dict]:
    """Static catalogue for tester UI — name, human label, tag."""
    rows: list[tuple[str, str, str]] = [
        ("docker_list_containers", "Containers", "system"),
        ("docker_system_info", "Docker Info", "system"),
        ("habitica_get_stats", "Habitica Stats", "personal"),
        ("habitica_get_tasks", "Habitica Tasks", "personal"),
        ("immich_get_memories", "Memories", "photos"),
        ("immich_list_albums", "Albums", "photos"),
        ("sonarr_list_series", "Sonarr Series", "arr"),
        ("sonarr_queue", "Sonarr Queue", "arr"),
        ("sonarr_calendar", "Sonarr Calendar", "arr"),
        ("sonarr_missing", "Sonarr Missing", "arr"),
        ("radarr_list_movies", "Radarr Movies", "arr"),
        ("radarr_queue", "Radarr Queue", "arr"),
        ("lidarr_list_artists", "Lidarr Artists", "arr"),
        ("lidarr_queue", "Lidarr Queue", "arr"),
        ("jellyfin_recently_added", "Jellyfin Recent", "media"),
        ("jellyseerr_requests", "Seerr Requests", "media"),
        ("qbittorrent_list", "Downloads", "media"),
        ("ha_get_states", "HA States", "home"),
        ("nextcloud_list_calendars", "NC Calendars", "cloud"),
        ("nextcloud_list_contacts", "NC Contacts", "cloud"),
        ("nextcloud_get_user_info", "NC User Info", "cloud"),
        ("nextcloud_get_activity", "NC Activity", "cloud"),
        ("nextcloud_get_notes", "NC Notes", "cloud"),
        ("nextcloud_list_shares", "NC Shares", "cloud"),
        ("nextcloud_list_files", "NC Files", "cloud"),
        ("nextcloud_get_notifications", "NC Notifications", "cloud"),
        ("omv_system_info", "OMV Info", "system"),
        ("omv_disk_health", "OMV Disks", "system"),
        ("comfyui_status", "ComfyUI Status", "ai"),
        ("comfyui_queue", "ComfyUI Queue", "ai"),
        ("comfyui_get_models", "ComfyUI Models", "ai"),
        ("get_context", "Date & Context", "utilities"),
        ("weather_current", "Weather Now", "utilities"),
        ("weather_forecast", "Forecast", "utilities"),
        ("weather_remember_city", "Remember weather city", "utilities"),
        ("web_search", "Web Search", "utilities"),
        ("wikipedia_summary", "Wikipedia summary", "utilities"),
        ("currency_convert", "Currency convert", "utilities"),
        ("currency_rates", "Currency rates", "utilities"),
        ("google_search", "Google search", "utilities"),
        ("n8n_list_workflows", "n8n Workflows", "automation"),
        ("n8n_get_executions", "n8n Executions", "automation"),
        ("uptime_status", "Uptime Kuma", "monitoring"),
        ("fs_list_shares", "FS Shares", "system"),
        ("obsidian_get_daily_note", "Daily Note", "notes"),
        ("syncthing_status", "Syncthing", "sync"),
        ("syncthing_folders", "ST Folders", "sync"),
        ("syncthing_devices", "ST Devices", "sync"),
        ("tailscale_status", "Tailscale", "network"),
        ("fail2ban_status", "Fail2ban", "security"),
        ("fal_generate_image", "fal Flux image", "ai"),
        ("fal_list_models_snippet", "fal Models (sample)", "ai"),
    ]
    try:
        from tools.public_apis_bulk import PUBLIC_CATALOG_META as _PCM

        rows = [*rows, *_PCM]
    except ImportError:
        pass
    out = []
    for name, label, tag in rows:
        out.append(
            {
                "name": name,
                "label": label,
                "tag": tag,
                "configured": is_tool_environment_ready(name),
            }
        )
    return out

