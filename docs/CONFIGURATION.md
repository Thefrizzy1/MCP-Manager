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
| `UI_PORT` | `8766` | Web dashboard port (`/app`) |
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
| ComfyUI MCP (comfy-mcp) | `COMFY_MCP_COMMAND` (set in the Docker image; else `comfy-mcp` on PATH — **.env only**, not editable in the dashboard), optional `COMFY_API_KEY`, `COMFY_MCP_ASSUME_CONSENT` (passed through) |
| fal.ai | `FAL_KEY` |
| Google Search | `GOOGLE_API_KEY`, `GOOGLE_CSE_ID` |
| YouTube | optional `YOUTUBE_API_KEY` (exact counts + publish dates; everything but trending works without it) |
| YouTube Studio & Search Console | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, optional `GOOGLE_OAUTH_REDIRECT_URI` — then **Settings → Google account → Connect** |
| Weather | `WEATHER_DEFAULT_LOCATION` |
| SSH hosts / SMB shares | `SSH_HOSTS`, `SMB_SHARES` (JSON arrays; managed via the dashboard) |

There are additional `*_URL` "bookmark" integrations (Audiobookshelf, Paperless,
Vaultwarden, Grafana, Pi-hole, …) that add dashboard cards without dedicated tools — see
`config.py` for the full list.

### YouTube Studio & Search Console (Google login)

Watch time, retention, the search terms that found your videos, impressions/CTR
and Search Console are owner-only, so they need one Google sign-in:

1. In Google Cloud Console, enable **YouTube Data API v3**, **YouTube Analytics
   API**, **YouTube Reporting API** and **Google Search Console API**.
2. OAuth consent screen: add yourself as a test user, then set publishing status
   to **In production**. Left in *Testing*, Google expires the login every 7 days.
3. Create an OAuth client of type **Desktop app**; paste its id and secret on the
   *YouTube Studio & Search Console* card.
4. **Settings → Google account → Connect.** Google redirects to
   `http://localhost:<UI_PORT>/api/v1/google/callback`. Opened on the server
   itself it finishes by itself; anywhere else the browser shows "can't connect" —
   copy that whole address into the box under Connect. (With an https name for
   Plutus, set `GOOGLE_OAUTH_REDIRECT_URI` to `https://<name>/api/v1/google/callback`
   and use a *Web application* client instead.)

The refresh token is stored in `data/google_oauth.json` (0600). Connecting also
registers YouTube's daily impressions report; the first files arrive ~48 h later
with the previous 30 days backfilled.

### ComfyUI MCP (comfy-mcp)

The Docker image ships Comfy-Org's `comfy-mcp` + `comfy-cli` in `/opt/comfy-mcp`
(their own venv — comfy-mcp needs mcp 2.x). Plutus serves its tools as `comfy_*`,
pointed at `COMFYUI_URL`: a loopback address means this machine, anything else a
remote GPU box, where runs, jobs, uploads and output fetches go remote and
install/lifecycle tools stay local. Consent prompts (node installs, version
switches) are declined — pre-authorise them with `COMFY_MCP_ASSUME_CONSENT` as
comfy-mcp documents. Build with `--build-arg WITH_COMFY_MCP=0` to leave it out.

> **Security:** `UI_PASSWORD`, `MCP_BEARER_TOKEN`, and all service credentials are
> stored in `.env`. `chmod 600 .env` and keep it out of VCS (already in `.gitignore`).
