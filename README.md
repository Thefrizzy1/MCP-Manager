# Plutus — MCP Manager

A single self-hosted **Model Context Protocol** server for a homelab, with a
full web app to configure it, watch its health, run agents, and hand ready-made
configs to any MCP client. ~193 tools across media, photos, home automation,
productivity, infrastructure, and public APIs — all behind one endpoint.

- **MCP endpoint:** `http://<host>:8765/mcp` (streamable HTTP; `/sse` also served)
- **Web app:** `http://<host>:8766/app` (HTTP Basic auth; `/` and `/ui` redirect here)

> 📚 **Full docs:** [Architecture](docs/ARCHITECTURE.md) ·
> [Security](docs/SECURITY.md) · [Operations](docs/OPERATIONS.md) ·
> [Configuration](docs/CONFIGURATION.md) · [Agents & Scheduler](docs/AGENTS.md) ·
> [Testing](docs/TESTING.md) · [Changelog](docs/CHANGELOG.md) ·
> [Contributing](docs/CONTRIBUTING.md)

---

## What it is

One MCP server exposes every homelab service through a single, authenticated
endpoint, so any MCP client (Claude Desktop/Code, Cursor, VS Code, n8n, …) can
drive your whole stack. The web app is a React desktop-style console (Vite +
React 19 + Tailwind 4 + lucide) served at `/app`, built into the image by a
multi-stage Docker build. It's the control centre: connections, health, agents,
files, settings, and a token-optimising tool slicer.

## The web app at a glance

| Section | What it does |
|---|---|
| **Dashboard** | Health overview — connection counts by status, tools, capabilities, recent tool runs. Ignored connections are excluded from the stats. |
| **Connections** | The core management surface (the old *Integrations* page is folded in here). One row per service with its icon, category, tool count, web address, and a traffic-light status. Configure, test, hide, and browse the catalog from here. |
| **Discover** | Scan a LAN host for known services and **configure them straight away**, or point the **API Discovery** wizard at any OpenAPI/FastAPI service to read its endpoints and add it as a connection. |
| **Slicer** | Preview and shrink the tool manifest exposed to clients by intent (e.g. `calendar tasks files`). |
| **Agents** | Launch, schedule, and monitor headless **Claude Code** agents that operate Plutus's own tools. Usage stats, live console, per-run permissions. |
| **AI Builder** | Describe an agent in plain language; Claude drafts its goal/prompt; review and launch. |
| **Files** | Browse the **internal research library** the agents write to, plus any mounted SMB/NAS shares. Preview, download, delete (path-confined, secrets redacted). |
| **Settings** | MCP endpoint & bearer token, client-config export, custom integrations, tool-category exposure, defaults, UI credentials, reset. |

### Connections — configure, test, manage

- **Icons + web address** per service, pulled from the registry and your live `.env`.
- **Inline Configure**: a form built from each service's env fields (URL, API key,
  …). Secrets are masked — leave a secret blank to keep the current value.
- **Two-stage testing** with a tri-state light:
  - **Test** = HTTP reachability probe.
  - **Try** = actually calls the service's tools and checks they return data.
  - 🟢 green = probe + tools OK · 🟠 orange = reachable but tools failing (or not
    yet tool-tested) · 🔴 red = unconfigured / unreachable.
- **Ignore / restore** a connection (🚫): ignored rows grey out and drop from the
  Dashboard stats, the counts, and the agent connection picker.
- **Sortable columns** — click a header to sort by it, click again to flip
  ascending/descending (▲/▼). Plus **Hide unconfigured** and **Show ignored**.
- **＋ Add** a custom connection, or **🧩 Catalog** to start from a popular service.

### Discover — set it up in one flow

- **Network scan**: probe a host (Docker + common ports); each hit gets a
  **Configure →** button that saves the detected URL and opens the full form so
  you can finish (add an API key) and land on Connections.
- **API Discovery (OpenAPI / FastAPI)**: enter a base URL; Plutus finds the spec
  (`/openapi.json`, `/swagger.json`, `/v3/api-docs`, …), lists every endpoint
  (method, path, params), and can save it as a connection.

## Install

**Recommended — pull the pre-built image:**

```bash
cp .env.example .env          # set UI_PASSWORD; fill in the services you use
chmod 600 .env
docker compose pull && docker compose up -d
```

Uses `ghcr.io/thefrizzy1/mcp-manager:latest`, published by GitHub Actions **only
after the test suite passes** (see [CI](#ci)). Update later with the same
`pull && up -d`.

**Alternative — build from source (e.g. a git-context compose):**

```bash
docker compose build --no-cache && docker compose up -d --force-recreate
```

`docker compose up --build` does the same for a local checkout. After deploy,
confirm the running version in **Settings → About** (or
`docker exec plutus-mcp cat core/version_info.py`).

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
| SSH / SMB | allowlisted remote commands; share browse/manage/mount |
| Ntfy | send notifications |
| Filesystem | list, read (secret-redacted), search, write, move |
| ComfyUI / fal.ai | image generation & workflow control |
| Public APIs | weather, maps, web/Google search, finance, trivia, Wikipedia, … |

Custom services (any HTTP API, or one discovered via OpenAPI) are added from the
Connections page and stored in `data/custom_integrations.json`.

## Connect an MCP client

**Settings → Connect a client** generates and downloads a ready-to-use config
for Claude Desktop, Claude Code, Cursor, VS Code, Cline, Windsurf, ChatGPT/OpenAI,
LM Studio, Open WebUI, or n8n — pre-filled with your URL and (optionally) a Bearer
token. A **Test connection** button verifies it first.

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

Claude Code, Cursor, VS Code, etc. connect to `http://<host>:8765/mcp` directly —
let the exporter emit the exact format for each.

## Agents & scheduler

The **Agents** workspace runs a headless **Claude Code** agent that operates
Plutus's own ~193 tools — e.g. *"find stuck \*arr queues and restart unhealthy
containers"* or a nightly research playbook — with a live console, cost tracking,
and a serial run queue.

- **Launch wizard**: name, model, schedule (run now / daily / weekly / cron),
  **write / publish switches**, and a **per-connection allow-list** of exactly
  which services the agent may touch.
- **Capability switches** (enforced via Claude Code `--disallowedTools`):
  **write** off = a read-only audit posture; **publish** off (the default)
  blocks outward actions — email, ntfy, webhooks, public GitHub issues/PRs,
  share links — even when write is on.
- **Per-connection ACL**: unchecked homelab connections are blocked for that run;
  web/search/weather/file utilities always stay available so research still
  works. Both ad-hoc **and scheduled** runs carry their own permission + ACL —
  running one "all tools" agent never silently escalates your schedules.
- **Usage**: runs-today vs a daily cap, remaining budget, all-time cost, queue
  depth, and account/auth state.
- **Sign in with your subscription** (session/OAuth token, not an API key):
  Settings → paste a `claude setup-token` token. `ANTHROPIC_API_KEY` stays a
  compose-only opt-in.

Schedule agent prompts, saved playbooks, or individual tool calls on a cron. See
[docs/AGENTS.md](docs/AGENTS.md).

## Security (summary)

- Web UI behind HTTP Basic auth (+ login lockout); app-wide CSRF Origin-check on
  every mutating request.
- Optional MCP Bearer auth (`MCP_REQUIRE_BEARER=true`), applied live without a
  restart.
- Docker writes off by default; SSH hosts read-only by default; filesystem
  confined to `FILESYSTEM_ALLOWED_PATHS` (symlink/`..`-resolved, boundary-aware)
  plus the internal library.
- Secrets in files/errors are redacted by default; the Configure form never
  returns stored secret values; `web_fetch` has an SSRF guard.
- Agents run with a real permission model, not all-or-nothing (see above).
- **Keep ports 8765/8766 off the public internet — LAN/Tailscale only.**

Full model, threat analysis, and hardening checklist: [docs/SECURITY.md](docs/SECURITY.md).

## Health monitoring

- **Connections → Test all** runs every service check and shows the full markdown
  health report.
- `POST /api/v1/health/regression-check?notify=1` runs the tool batch, diffs
  against a saved baseline, and ntfy-alerts on tools that worked before and fail
  now. Schedule it via the Agents scheduler, cron, or n8n — see
  [docs/OPERATIONS.md](docs/OPERATIONS.md#3-health--monitoring).

## Remote access via Tailscale (optional)

```bash
tailscale serve --bg --https=443 http://localhost:8766    # web app
```

Set `PUBLIC_MCP_BASE=https://<name>.<ts-net>` in `.env` so the client exporter
emits HTTPS configs. Connect MCP clients to `https://<name>.<ts-net>/mcp`.

## CI

`.github/workflows/docker-publish.yml` runs on every push and pull request:
installs deps, `node --check`s the SPA bundle, runs `pyflakes`, and runs the
offline `pytest` suite. The image build **depends on the tests passing**, so a
broken commit never publishes; pull requests are tested but not published.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest              # offline backend suite (no network)

# Frontend (React app in ui/web/):
npm --prefix ui/web install
npm --prefix ui/web run build   # -> ui/static/dist (served at /app)
npm --prefix ui/web run dev     # dev server on :5173, proxies /api to :8766
```

The UI lives in `ui/web/` (Vite). The build outputs hashed assets to
`ui/static/dist`; FastAPI serves them at `/app` (falling back to the legacy SPA
only if `dist` is absent). No manual cache-busting — Vite hashes filenames. See
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

**Updating is non-destructive:** `docker compose pull && docker compose up -d`
preserves your settings, connections, profiles, and keys (data/, config/ and the
mounted `.env` persist).
