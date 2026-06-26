# Plutus MCP Server

A single self-hosted **Model Context Protocol** server for a homelab, with a web
dashboard for configuration, health, and client setup. ~193 tools across media, photos,
home automation, productivity, infrastructure, and public APIs.

- **MCP endpoint:** `http://<host>:8765/mcp` (streamable HTTP)
- **Web dashboard:** `http://<host>:8766/ui` (HTTP Basic auth)

> 📚 **Full docs:** [Architecture](docs/ARCHITECTURE.md) ·
> [Security](docs/SECURITY.md) · [Operations](docs/OPERATIONS.md) ·
> [Configuration](docs/CONFIGURATION.md) · [Testing](docs/TESTING.md) ·
> [Changelog](docs/CHANGELOG.md) · [Contributing](docs/CONTRIBUTING.md)

## Tools included

| Service | Tools |
|---|---|
| Jellyfin | search, recently added |
| Sonarr | search, list, add, queue, calendar, missing |
| Radarr / Lidarr | search, list, add, queue |
| Jellyseerr | request, list requests |
| qBittorrent | list, pause, resume, delete |
| Habitica | tasks, stats, score, add todo, add/delete task |
| Nextcloud | calendars, events, tasks, notes, contacts, files |
| Home Assistant | states, search, call service, on/off |
| Immich | search, albums, memories, people |
| Docker / OMV | containers, logs, start/stop/restart; disk & system info |
| SSH / SMB | allowlisted remote commands; share browse/manage |
| Ntfy | send notifications |
| Filesystem | list, read (secret-redacted), search, write, move |
| Public APIs | weather, maps, web/Google search, finance, trivia, … |

## Setup

1. `cp .env.example .env` and fill in service URLs/keys (see [Configuration](docs/CONFIGURATION.md)). **Set `UI_PASSWORD`.**
2. `chmod 600 .env`
3. Edit `docker-compose.yml`: set the NAS volume path/UUID for your host.
4. Deploy:

   ```bash
   docker compose up -d
   ```

5. Open the dashboard: `http://<host>:8766/ui` (login `admin` / your `UI_PASSWORD`).

## Connect an MCP client

The dashboard (**Settings → Connection Manager**) generates and downloads a ready-to-use
config for any client — Claude Desktop, Claude Code, Cursor, VS Code, Cline, Windsurf,
ChatGPT/OpenAI, LM Studio, Open WebUI, n8n — pre-filled with your URL and (optionally) a
Bearer token. **Test MCP connection** verifies it before you paste it in.

Claude Desktop bridges to the remote endpoint via `mcp-remote`:

```json
{
  "mcpServers": {
    "plutus": {
      "command": "npx",
      "args": ["mcp-remote", "http://<host>:8765/mcp", "--allow-http"]
    }
  }
}
```

Claude Code, Cursor, VS Code, etc. connect to `http://<host>:8765/mcp` directly — let the
Connection Manager emit the exact format for each.

## Security (summary)

- Web UI behind HTTP Basic auth (+ login lockout); CSRF Origin-check on mutating requests.
- Optional MCP Bearer auth (`MCP_REQUIRE_BEARER=true`), applied live.
- Docker writes off by default; SSH hosts read-only by default; filesystem confined to `FILESYSTEM_ALLOWED_PATHS`.
- Secrets in files/errors are redacted by default; `web_fetch` has an SSRF guard.
- **Keep ports 8765/8766 off the public internet — LAN/Tailscale only.**

Full model, threat analysis, and hardening checklist: [docs/SECURITY.md](docs/SECURITY.md).

## Health monitoring

`POST /api/v1/health/regression-check?notify=1` runs the tool batch, diffs against a
saved baseline, and ntfy-alerts on tools that worked before and fail now. Schedule it via
cron or n8n — see [docs/OPERATIONS.md](docs/OPERATIONS.md#3-health--monitoring).

## Remote access via Tailscale (optional)

```bash
tailscale serve --bg --https=443 http://localhost:8766    # dashboard
```

Set `PUBLIC_MCP_BASE=https://<name>.<ts-net>` in `.env` so the Connection Manager emits
HTTPS configs. Connect MCP clients to `https://<name>.<ts-net>/mcp`.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest        # offline suite
```

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
