# Changelog

Notable changes to Plutus. Dates are approximate; this project is single-maintainer.

## 6.0.0 — full rebuild: new React UI, MCP primitives, token slicer

A ground-up rebuild. The framework-free teal SPA (the "Unreleased" notes below)
was replaced by a new React desktop-aesthetic UI, and the backend gained real MCP
primitives (profiles, prompts, resources, annotations, an App widget).

### BREAKING

- **One-time reset of tool-exposure settings.** The global tool gate
  (`data/plutus_tool_gate.json`, monkeypatched onto the SDK) is gone; it is
  replaced by per-profile MCP endpoints and a category-based token slicer.
  Everything else in `data/` and your `.env` is preserved.
- **Non-destructive updates from here on.** Mount `./.env:/app/.env` (see the
  updated `docker-compose.yml`) so settings saved from the UI survive
  `docker compose pull && up -d`. Without it, UI-saved keys reset on every update.

### Added

- **New desktop UI** (Vite + React 19 + Tailwind 4 + lucide), served at `/app`;
  real brand logos (offline `simple-icons`), light + dark, dense professional look.
- **Token-reducing tool slicer** on the Dashboard — disable tool categories to
  shrink the `/mcp` manifest and cut prompt tokens, with a live estimate.
- **MCP primitives:** per-profile endpoints (`/mcp/p/<name>`), playbooks exposed
  as prompts, `plutus://` resources (health, connections, agent-runs, library —
  library reads go through path-guard + redaction), all four annotation hints on
  every tool, and an MCP App widget (`plutus_status`, `ui://plutus/connections`).
- **Connection health states:** Online / Offline / Auth error / Rate limited /
  API error / Not configured — see *why* a service is unavailable, not just that.
- **Discover:** the found IP:port auto-fills the Configure form, and a
  "Configure all" batch-saves every service found on a host in one click.
- **Multi-stage Docker image** builds and ships the React UI; CI builds it too.

### Changed

- **Backend restructured:** `main.py` 1,575 → 235 lines; every endpoint moved to
  `ui/api/*` routers with auth attached per-router (a new route can't be unguarded).
- The **agent ACL now derives from tool annotations** (`openWorld`/`destructive`)
  with the curated block-lists kept as a safety-net override.
- MCP protocol version pinned in one place (`MCP_PROTOCOL_VERSION`).

## Unreleased — Agents page redesign, app-wide teal palette, audit

- **Agents page redesigned** (`ui/agents_page.py`): a persistent launcher (prompt +
  one-click playbook chips) at the top, collapsible live console, and powerful controls
  (permissions, model, login, scheduling, editing) organised into clean tabbed cards —
  progressive disclosure. New button/card/field/tab design system in `dashboard.css`.
- **App-wide colour palette** recolored to an aqua/teal system (turquoise primary,
  verdigris secondary, light-green success, aquamarine highlight, dark-teal depth) across
  the whole dashboard, both dark and light modes, via CSS variables.
- **Fixed the broken "log in via browser" flow** — removed the in-container OAuth capture
  (a headless container can't run the browser handshake) and its endpoints; the reliable
  token-paste path (`claude setup-token` → paste) is now the single, clearly-documented way.
- Fixed the header showing "not connected" when a session token was set (`loadState`
  didn't handle the `session_token` auth mode; also corrected a stale CSS class).
- Documented the agent audit in `docs/AGENT_AUDIT.md`.
- Verified every agent/schedule endpoint end-to-end via a TestClient smoke (24/24).

## Unreleased — agent hardening & UX overhaul

Audit-driven pass on the agent subsystem.

### Security
- **Tool permission system** (`core/agent_permissions.py`) — the headless agent's tool
  access is capped by a level: `strict_read` / `safe` (default) / `all`, enforced via Claude
  Code `--disallowedTools`. `safe` allows note-writing but blocks docker control, deletes,
  ssh, HA control, email, torrent delete, n8n triggers, and image generation. Per-playbook
  override. Addresses the prompt-injection blast-radius of research playbooks reading
  untrusted web pages. Documented in `docs/SECURITY.md`.
- `data/agent_mcp.json` (holds the MCP bearer token) written `0600`.

### Web login (session/OAuth token, never an API key)
- **Connect Claude account** in the Agents Settings tab: paste a `claude setup-token`
  token, or run the OAuth flow in-dashboard (get the link, approve, paste the code).
  Stored as `CLAUDE_CODE_OAUTH_TOKEN` and used immediately — no `docker exec`.
  (`core/agent_login.py`, `/api/v1/agent/login/*`.)

### Operability
- **Stop button** cancels the running agent (`/api/v1/agent/cancel`).
- **Run queue** — runs are queued (serial) instead of rejected when one is active.
- **Daily run cap** (`max_runs_per_day`) protects the plan usage window.
- **Schedule presets** — Daily/Hourly/Every-N/Weekly builder generates cron; no hand-writing.
- **Preview** a playbook's fully-rendered prompt before running.
- Per-playbook **model** and **permission** overrides; **build-with-Claude** refuses while a
  run is active (no double `claude` processes).
- Header shows billing mode, runs-today/cap, and queue depth.

## Unreleased — full Agents page

- **Dedicated `/agents` full page** (`ui/agents_page.py` + `ui/static/agents.js`) with
  tabs: Run, Playbooks, Schedules, Settings, History — replacing the cramped footer
  panel (the dashboard button now links to it).
- **Build an agent with Claude** — describe a task in plain language and the running
  Claude Code drafts a playbook prompt (`core/agent_runner.build_text` +
  `agent_tasks.build_meta_prompt`, `POST /api/v1/agent/tasks/build`).
- **Output destination settings** — choose where the knowledge library lives
  (Obsidian vault folder or a filesystem path); `resolve_library()` fills `{{LIBRARY}}`
  and a new `{{OUTPUT_HINT}}` placeholder that tells the agent which tools to persist with.
- **Per-run ntfy notifications** (all runs or failures only).
- Model / tools / timeout / cost guard all editable in the Settings tab.

## Unreleased — agents, scheduler & pullable image

### Features
- **Research playbooks + knowledge library** (`core/agent_tasks.py`) — named,
  editable research tasks seeded with a starter set (competitor research, AI/ComfyUI
  trend scan, channel audit, script draft, weekly digest). Each is instructed to read
  an accumulating knowledge library first, then add/refine notes — so scheduled runs
  compound. Schedules can reference a playbook by id (schedule kind `task`). Endpoints
  `/api/v1/agent/tasks*`; surfaced in the Agents panel with a configurable library folder.
- **Headless agent runner** (`core/agent_runner.py`) — runs Claude Code
  (`claude -p --output-format stream-json`) as a subprocess and hands it Plutus's
  own MCP endpoint, so an agent can operate all ~193 homelab tools. Live SSE
  console, per-run cost/turn tracking, run history under `data/agent_runs/`,
  cost/timeout guards. Endpoints under `/api/v1/agent/*`.
- **Scheduler** (`core/scheduler.py` + `core/schedule_store.py`) — "schedule
  anything": cron-triggered **agent** prompts or **tool** calls. APScheduler
  runtime (lazy-imported; degrades gracefully if absent). Endpoints under
  `/api/v1/schedules/*`. New dashboard **Agents** panel.
- Offline tests for the store, cron validation, command building, and stream-json
  event parsing (`tests/test_schedule_store.py`, `tests/test_agent_runner.py`).

### Deploy
- **Pre-built image + `docker compose pull`** — `.github/workflows/docker-publish.yml`
  builds and pushes `ghcr.io/thefrizzy1/mcp-manager` to GHCR on push/tag; compose
  now references the image (build still works). No more mandatory local build.
- Dockerfile installs Node.js + Claude Code so the agent works out of the box;
  compose mounts `~/.claude` for login persistence and `./data` for state.

## Unreleased — hardening, reliability & docs pass

A structured audit (security / architecture / SRE standpoints) drove a coherent round
of fixes. All changes ship with offline tests where the logic allows.

### Security
- **Fixed command injection in `ssh_exec`** — host/arg are now charset-validated and the
  host is `--`-separated so shell metacharacters and option injection are rejected.
  (`tools/infrastructure.py`, `tests/test_ssh_exec_validation.py`)
- **Fixed bearer-auth staleness across processes** — the MCP gate now reads the
  token/flag live from `.env` (short TTL cache), so enabling/rotating it in the UI
  applies without a restart. (`core/mcp_bearer_middleware.py`, `tests/test_bearer_live.py`)
- **Added CSRF protection** — an Origin-check middleware rejects cross-site
  state-changing requests; non-browser clients are unaffected; `PLUTUS_DISABLE_CSRF`
  escape hatch. (`main.py`)
- **Added SSRF guard to `web_fetch`** — blocks non-HTTP(S) schemes and hosts resolving to
  private/loopback/link-local/metadata addresses. (`core/ssrf_guard.py`)
- **Path-confinement hardening** — boundary-aware check (realpath + boundary test)
  replaces a raw string prefix; shared by fs tools and SMB browse.
  (`core/path_guard.py`)
- **Secret-leak fixes** — `fs_read_file` masks secrets by default (`core/redact.py`,
  `reveal_secrets` opt-out); `_handle_error` suppresses upstream bodies/exception text
  unless `PLUTUS_VERBOSE_ERRORS=1`; `smb_add_share` no longer echoes the password.
- **Error-handling sweep** — raw `return f"Error: {e}"` across tools routed through
  `_handle_error` so nothing bypasses the redaction layer.

### Reliability / ops
- **Healthcheck now means "working"** — `/server/health` TCP-probes the MCP process and
  returns 503 if it's down; the Docker healthcheck is `UI_ENABLED`-aware (skips cleanly
  in MCP-only mode). (`main.py`, `Dockerfile`, `docker-compose.yml`)
- **UI watchdog + graceful shutdown** — the main process exits (for container restart)
  if the UI child dies; SIGTERM/SIGINT terminate+join the child; stale `*.tmp` files are
  swept on boot. (`main.py`)
- **Persisted `data/`** — added `./data:/app/data` volume so the health baseline, beta
  cache, and uploaded CA survive rebuilds. (`docker-compose.yml`)
- **Regression alerts that don't go silent** — the baseline no longer overwrites a
  known-good entry with a failure, so a regression keeps alerting until it recovers.
  (`core/health_regression.py`)
- Removed dead, unbounded `append_log_line` log writer. (`core/recent_runs.py`)

### Design / quality
- **Unified the two `.env` writers** into `core/env_store.py` (atomic, validated,
  cfg-syncing); `main.py` and `tools/ssh_smb.py` now share it.
- Extracted testable helpers (`path_guard`, `ssrf_guard`, env parsing, ssh validators).

### Features
- **Connection Manager** — `core/mcp_export.py` + `/api/v1/mcp/connections` +
  `/api/v1/mcp/selftest` + Settings panel: downloadable client configs (Claude
  Desktop/Code, Cursor, VS Code, Cline, Windsurf, ChatGPT/OpenAI, LM Studio, Open WebUI,
  n8n, generic) with an optional embedded bearer token and a Test-connection button.
- **Health regression endpoint** — `POST /api/v1/health/regression-check` for scheduled
  alerting on newly-broken tools.
- **`habitica_delete_task`** tool added (enables the Habitica reversible round-trip).

### Tooling / hygiene
- Added the offline `pytest` suite (`tests/`, `pytest.ini`, `requirements-dev.txt`).
- Added `.gitignore` and `.dockerignore` (keep `.env`/`data/` out of VCS and image).
- Pinned `requirements.txt` with major-version upper caps.
- Repointed dead `boredapi.com` to the maintained mirror; clearer OMV non-JSON error.
- `web_fetch` now decodes all HTML entities via `html.unescape`.
- `FILESYSTEM_ALLOWED_PATHS` parser tolerates a Python/JSON list-literal value.
- Documentation suite under `docs/` and a corrected `README.md`.
