"""
config.py — Loads all configuration from environment variables.
All settings are optional — tools gracefully disable if not configured.
"""

import os
import re
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Used when UI_PASSWORD is not set in the environment (first run / fresh .env).
DEFAULT_UI_PASSWORD = "adminadmin"


def _get(key: str, default: str = "") -> str:
    raw = os.getenv(key, default).strip()
    # Strip trailing slashes from URL-shaped values so tools can safely concatenate
    # `f"{cfg.foo_url}/path"` without ever producing `"//path"`. URL `_KEY` names
    # in our env (JELLYFIN_URL, NEXTCLOUD_URL, …) all denote a base URL; we never
    # want the trailing slash preserved.
    if key.endswith("_URL") and raw.startswith(("http://", "https://")):
        raw = raw.rstrip("/")
    return raw


def _get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("true", "1", "yes")


def parse_csv_paths(raw: str) -> list[str]:
    """Parse FILESYSTEM_ALLOWED_PATHS, tolerating a Python/JSON list literal.

    Accepts both the documented CSV form ``/a,/b`` and the common mistake
    ``['/a', '/b']`` (which a naive comma-split would turn into broken entries
    like ``['/a`` and ``/b']``). Strips surrounding brackets and per-item quotes.
    """
    s = (raw or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    out: list[str] = []
    for part in s.split(","):
        cleaned = part.strip().strip("'\"").strip()
        if cleaned:
            out.append(cleaned)
    return out


class Config(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    # Server
    mcp_host: str = _get("MCP_HOST", "0.0.0.0")
    mcp_port: int = int(_get("MCP_PORT", "8765"))
    ui_port: int = int(_get("UI_PORT", "8766"))
    ui_enabled: bool = _get_bool("UI_ENABLED", True)
    ui_username: str = _get("UI_USERNAME", "admin")
    ui_password: str = _get("UI_PASSWORD", DEFAULT_UI_PASSWORD)
    # HTTPS base behind Tailscale serve/funnel or proxy (path /mcp appended in UI)
    public_mcp_base: str = _get("PUBLIC_MCP_BASE", "")
    mcp_lan_host: str = _get("MCP_LAN_HOST", "192.168.1.111")
    mcp_require_bearer: bool = _get_bool("MCP_REQUIRE_BEARER", False)

    weather_default_location: str = _get("WEATHER_DEFAULT_LOCATION", "Hamburg")

    # Jellyfin
    jellyfin_url: str = _get("JELLYFIN_URL")
    jellyfin_api_key: str = _get("JELLYFIN_API_KEY")
    jellyfin_user_id: str = _get("JELLYFIN_USER_ID")

    # Sonarr
    sonarr_url: str = _get("SONARR_URL")
    sonarr_api_key: str = _get("SONARR_API_KEY")

    # Radarr
    radarr_url: str = _get("RADARR_URL")
    radarr_api_key: str = _get("RADARR_API_KEY")

    # Lidarr
    lidarr_url: str = _get("LIDARR_URL")
    lidarr_api_key: str = _get("LIDARR_API_KEY")

    # Jellyseerr
    jellyseerr_url: str = _get("JELLYSEERR_URL")
    jellyseerr_api_key: str = _get("JELLYSEERR_API_KEY")

    # qBittorrent
    qbittorrent_url: str = _get("QBITTORRENT_URL")
    qbittorrent_username: str = _get("QBITTORRENT_USERNAME", "admin")
    qbittorrent_password: str = _get("QBITTORRENT_PASSWORD")

    # Immich
    immich_url: str = _get("IMMICH_URL")
    immich_api_key: str = _get("IMMICH_API_KEY")

    # Home Assistant
    ha_url: str = _get("HA_URL")
    ha_token: str = _get("HA_TOKEN")

    # Habitica
    habitica_url: str = _get("HABITICA_URL")
    habitica_user_id: str = _get("HABITICA_USER_ID")
    habitica_api_token: str = _get("HABITICA_API_TOKEN")

    # Nextcloud
    nextcloud_url: str = _get("NEXTCLOUD_URL")
    nextcloud_username: str = _get("NEXTCLOUD_USERNAME")
    nextcloud_password: str = _get("NEXTCLOUD_PASSWORD")

    # Ntfy
    ntfy_url: str = _get("NTFY_URL")
    ntfy_username: str = _get("NTFY_USERNAME")
    ntfy_password: str = _get("NTFY_PASSWORD")
    ntfy_default_topic: str = _get("NTFY_DEFAULT_TOPIC", "assistant")

    # Fail2ban — optional bookmark only (no HTTP API)
    fail2ban_web_url: str = _get("FAIL2BAN_WEB_URL")

    # Docker
    docker_socket: str = _get("DOCKER_SOCKET", "/var/run/docker.sock")
    docker_write_enabled: bool = _get_bool("DOCKER_WRITE_ENABLED", False)

    # OMV
    omv_url: str = _get("OMV_URL")
    omv_username: str = _get("OMV_USERNAME")
    omv_password: str = _get("OMV_PASSWORD")

    # MCP Auth
    mcp_bearer_token: str = _get("MCP_BEARER_TOKEN")

    # Syncthing
    syncthing_url: str = _get("SYNCTHING_URL", "http://192.168.1.111:8384")
    syncthing_api_key: str = _get("SYNCTHING_API_KEY")

    # SMTP (for Proton Bridge or other email)
    smtp_host: str = _get("SMTP_HOST", "127.0.0.1")
    smtp_port: str = _get("SMTP_PORT", "1025")
    smtp_username: str = _get("SMTP_USERNAME")
    smtp_password: str = _get("SMTP_PASSWORD")

    # ComfyUI
    comfyui_url: str = _get("COMFYUI_URL")

    # Obsidian REST API
    obsidian_url: str = _get("OBSIDIAN_URL", "http://192.168.1.5:27124")
    obsidian_api_key: str = _get("OBSIDIAN_API_KEY")

    # n8n
    n8n_url: str = _get("N8N_URL", "http://192.168.1.111:5678")
    n8n_api_key: str = _get("N8N_API_KEY")

    # Uptime Kuma
    uptime_kuma_url: str = _get("UPTIME_KUMA_URL")

    # fal.ai
    fal_key: str = _get("FAL_KEY")

    # Google Programmable Search Engine (Custom Search JSON API)
    google_api_key: str = _get("GOOGLE_API_KEY")
    google_cse_id: str = _get("GOOGLE_CSE_ID")

    # Dashboard extras (bookmark integrations)
    audiobookshelf_url: str = _get("AUDIOBOOKSHELF_URL")
    audiobookshelf_api_key: str = _get("AUDIOBOOKSHELF_API_KEY")
    kavita_url: str = _get("KAVITA_URL")
    paperless_url: str = _get("PAPERLESS_URL")
    paperless_token: str = _get("PAPERLESS_TOKEN")
    vaultwarden_url: str = _get("VAULTWARDEN_URL")
    portainer_url: str = _get("PORTAINER_URL")
    gitea_url: str = _get("GITEA_URL")
    grafana_url: str = _get("GRAFANA_URL")
    prometheus_url: str = _get("PROMETHEUS_URL")
    pihole_url: str = _get("PIHOLE_URL")
    adguard_url: str = _get("ADGUARD_URL")
    transmission_url: str = _get("TRANSMISSION_URL")
    sabnzbd_url: str = _get("SABNZBD_URL")
    sabnzbd_api_key: str = _get("SABNZBD_API_KEY")
    bookstack_url: str = _get("BOOKSTACK_URL")
    minio_url: str = _get("MINIO_URL")
    minio_root_user: str = _get("MINIO_ROOT_USER")
    minio_root_password: str = _get("MINIO_ROOT_PASSWORD")
    matrix_homeserver_url: str = _get("MATRIX_HOMESERVER_URL")
    ghost_url: str = _get("GHOST_URL")
    wikijs_url: str = _get("WIKIJS_URL")

    netdata_url: str = _get("NETDATA_URL")
    traefik_url: str = _get("TRAEFIK_URL")
    mealie_url: str = _get("MEALIE_URL")
    homepage_url: str = _get("HOMEPAGE_URL")
    calibre_web_url: str = _get("CALIBRE_WEB_URL")
    tautulli_url: str = _get("TAUTULLI_URL")
    stirling_pdf_url: str = _get("STIRLING_PDF_URL")
    filebrowser_url: str = _get("FILEBROWSER_URL")
    nginx_proxy_manager_url: str = _get("NGINX_PROXY_MANAGER_URL")

    # Filesystem
    filesystem_allowed_paths: list[str] = parse_csv_paths(
        _get("FILESYSTEM_ALLOWED_PATHS", "/01_Offene_Jobs,/Hausatredies,/Ablage,/Backup")
    )

    # SSH Hosts (JSON array stored as env var)
    # Format: '[{"name":"plutus","host":"192.168.1.111","user":"root","key":"/root/.ssh/id_rsa","readonly":true}]'
    ssh_hosts_json: str = _get("SSH_HOSTS", "[]")

    # SMB Shares (JSON array stored as env var)
    # Format: '[{"name":"Offene Jobs","server":"192.168.1.111","share":"01_Offene_Jobs","user":"friso","password":"","mount":"/mnt/jobs"}]'
    smb_shares_json: str = _get("SMB_SHARES", "[]")

    # MCP connection settings
    mcp_allowed_origins: str = _get("MCP_ALLOWED_ORIGINS", "*")
    mcp_session_timeout: int = int(_get("MCP_SESSION_TIMEOUT", "3600"))

    def is_configured(self, *keys: str) -> bool:
        """Check if all given config keys are non-empty."""
        return all(bool(getattr(self, k, "")) for k in keys)


def allow_empty_ui_password() -> bool:
    """Only for isolated dev — never on a LAN-facing host."""
    return os.getenv("PLUTUS_ALLOW_EMPTY_UI_PASSWORD", "").strip().lower() in ("1", "true", "yes")


_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,62}$")
# Keys rejected for POST /env/save — avoids tampering with process/shell environment.
_BLOCKED_UI_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "PATHEXT",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
        "PYTHONUSERBASE",
        "PYTHONEXECUTABLE",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "DYLD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "HOME",
        "USER",
        "USERNAME",
        "LOGNAME",
        "SHELL",
        "TZ",
        "TERM",
        "TMPDIR",
        "TEMP",
        "TMP",
        "WINDIR",
        "SYSTEMROOT",
        "COMSPEC",
    }
)


def is_ui_writable_env_key(name: str) -> bool:
    n = (name or "").strip()
    return bool(_ENV_KEY_RE.fullmatch(n)) and n not in _BLOCKED_UI_ENV_KEYS


# Singleton
cfg = Config()
