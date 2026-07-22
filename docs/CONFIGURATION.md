# Plutus — Configuration Reference

All configuration is environment-driven, loaded from `.env` into the `cfg` singleton
(`config.py`). Copy `.env.example` to `.env` and fill in what you use — every service
is optional and its tools self-disable with a clear "not configured" message when keys
are missing.

> **Apply changes:** restart Plutus (`docker compose restart plutus-mcp`) after editing
> `.env`. The only setting that applies live is **bearer auth**
> (`MCP_REQUIRE_BEARER` / `MCP_BEARER_TOKEN`).

The canonical writer is `core/env_store.py` (atomic, key-allowlisted, newline-rejecting).
The dashboard's Settings panels write through it.

---

## Server & UI

| Key | Default | Meaning |
|---|---|---|
| `MCP_HOST` | `0.0.0.0` | MCP bind address |
| `MCP_PORT` | `8765` | MCP streamable-HTTP port (`/mcp`) |
| `UI_PORT` | `8766` | Web dashboard port (`/ui`) |
| `UI_ENABLED` | `true` | `false` = MCP-only (no dashboard, lower RAM) |
| `UI_USERNAME` | `admin` | Dashboard Basic-auth user |
| `UI_PASSWORD` | `adminadmin` | Dashboard Basic-auth password — **set this** |
| `PUBLIC_MCP_BASE` | — | Public HTTPS base (Tailscale/Caddy), e.g. `https://mcp.<ts-net>` |
| `MCP_LAN_HOST` | `192.168.1.111` | LAN host used in generated URLs |
| `MCP_REQUIRE_BEARER` | `false` | Require `Authorization: Bearer` on `/mcp` (applies live) |
| `MCP_BEARER_TOKEN` | — | The bearer token (generate via Settings) |

## Behaviour flags

| Key | Default | Meaning |
|---|---|---|
| `PLUTUS_VERBOSE_ERRORS` | `false` | Echo upstream bodies / exception text into tool errors (may leak secrets) |
| `PLUTUS_DISABLE_CSRF` | `false` | Disable the Origin/CSRF check (only for unusual proxy setups) |
| `PLUTUS_ALLOW_EMPTY_UI_PASSWORD` | `false` | Dev only — serve the UI with no password (never on a LAN-facing host) |
| `PLUTUS_AUTO_INSTALL` | `false` | Auto-`pip install` missing deps at startup (dev) |
| `PLUTUS_LOG_LEVEL` | `INFO` | Python log level |
| `PLUTUS_UPDATES_REPO` | — | `owner/repo` for the Settings → Updates check |

## Filesystem

| Key | Default | Meaning |
|---|---|---|
| `FILESYSTEM_ALLOWED_PATHS` | `/01_Offene_Jobs,/Hausatredies,/Ablage,/Backup` | Comma-separated roots the fs tools may touch. Also tolerates a `['/a','/b']` list literal. |

## Services (set the ones you use)

Each service typically needs a `*_URL` and, where applicable, an API key/credentials.
Tools stay disabled until their required keys are present.

| Service | Keys |
|---|---|
| Jellyfin | `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_USER_ID` |
| Sonarr / Radarr / Lidarr | `SONARR_URL`+`SONARR_API_KEY`, `RADARR_*`, `LIDARR_*` |
| Jellyseerr | `JELLYSEERR_URL`, `JELLYSEERR_API_KEY` |
| qBittorrent | `QBITTORRENT_URL`, `QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD` |
| Immich | `IMMICH_URL`, `IMMICH_API_KEY` |
| Home Assistant | `HA_URL`, `HA_TOKEN` |
| Habitica | `HABITICA_URL`, `HABITICA_USER_ID`, `HABITICA_API_TOKEN` |
| Nextcloud | `NEXTCLOUD_URL`, `NEXTCLOUD_USERNAME`, `NEXTCLOUD_PASSWORD` (app password) |
| Ntfy | `NTFY_URL`, `NTFY_DEFAULT_TOPIC`, optional `NTFY_USERNAME`/`PASSWORD` |
| Docker | `DOCKER_SOCKET` (`/var/run/docker.sock`), `DOCKER_WRITE_ENABLED` (default `false`) |
| OMV | `OMV_URL`, `OMV_USERNAME`, `OMV_PASSWORD` |
| Syncthing | `SYNCTHING_URL`, `SYNCTHING_API_KEY` |
| Obsidian | `OBSIDIAN_URL`, `OBSIDIAN_API_KEY` |
| n8n | `N8N_URL`, `N8N_API_KEY` |
| ComfyUI | `COMFYUI_URL` |
| fal.ai | `FAL_KEY` |
| Google Search | `GOOGLE_API_KEY`, `GOOGLE_CSE_ID` |
| Weather | `WEATHER_DEFAULT_LOCATION` |
| SSH hosts / SMB shares | `SSH_HOSTS`, `SMB_SHARES` (JSON arrays; managed via the dashboard) |

There are additional `*_URL` "bookmark" integrations (Audiobookshelf, Paperless,
Vaultwarden, Grafana, Pi-hole, …) that add dashboard cards without dedicated tools — see
`config.py` for the full list.

> **Security:** `UI_PASSWORD`, `MCP_BEARER_TOKEN`, and all service credentials are
> stored in `.env`. `chmod 600 .env` and keep it out of VCS (already in `.gitignore`).
