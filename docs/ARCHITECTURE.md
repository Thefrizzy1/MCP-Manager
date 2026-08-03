# Plutus — Architecture

Plutus is a single self-hosted **Model Context Protocol (MCP) server** for a homelab,
paired with a **web dashboard** for configuration, health, and discovery. One process
tree exposes ~193 tools spanning media (Jellyfin, *arr, qBittorrent), photos (Immich),
home automation (Home Assistant), productivity (Nextcloud, Habitica, Obsidian),
infrastructure (Docker, OMV, SSH, SMB, Syncthing), and a large catalogue of public
APIs (weather, maps, search, finance, trivia).

---

## 0. v6 module map (the rebuild)

v6 restructured the codebase. The high-level shape:

```
main.py                 thin orchestrator: dependency check, two-process launch
ui/runtime.py           app-wide singletons (FastMCP `mcp`, tools adapter, scheduler,
                        agent orchestration, health cache) + build_mcp / build_mcp_asgi_app
ui/api/__init__.py      build_ui_app() — assembles routers, mounts, CSRF guard
ui/api/deps.py          verify_auth + CSRF origin guard (auth attached per-router)
ui/api/*.py             HTTP endpoints by surface (connections, discover, catalog,
                        profiles, agents, health, settings, system, files, public)
ui/web/                 React UI (Vite + React 19 + Tailwind 4 + lucide) → ui/static/dist,
                        served at /app (falls back to the legacy SPA if dist is absent)

core/profiles.py        MCP profiles: named tool subsets + registration-time tool_filter
                        (API + /mcp/p/<name> endpoints only — no UI, see below)
core/tool_exposure.py   the "slicer": category exposure on the served /mcp (token saver)
core/tool_annotations.py completes all four annotation hints at registration
tools/prompts.py        playbooks → MCP prompts
tools/resources.py      plutus:// resources (through path_guard + redact)
tools/apps.py           MCP App widget (plutus_status + ui://plutus/connections)
```

**MCP serving.** The MCP process serves `ui.runtime.build_mcp_asgi_app()`: the full
surface at `/mcp` (honouring the tool-exposure slicer), one endpoint per profile at
`/mcp/p/<name>`, all behind `MCPBearerGateMiddleware`. Each FastMCP sub-app is asked
to route *itself* at that absolute path (`settings.streamable_http_path`) and its
route is lifted into the outer app — do **not** `Mount()` them, which nests FastMCP's
own `/mcp` route into `/mcp/mcp` and turns the advertised `/mcp` into a 307→404.
Sub-instances must also inherit `cfg.mcp_host`, or FastMCP's loopback default
auto-enables DNS-rebinding protection and they 421 every non-localhost client.
Tool filtering is done at
*registration* (fail-safe) — a disallowed tool is never registered on that instance;
there is no list-time monkeypatch. Profiles/exposure are **restart-to-apply** (the MCP
server is a separate process from the UI). The old global tool gate is gone.

**Agent scoping is one dial, not three.** A launched agent is governed solely by the
MCP connections ticked in the launch wizard: selecting a connection grants read *and*
write on that service's tools. The permission levels (`strict_read`/`safe`/`all`) no
longer narrow a run — two overlapping gates made it impossible to tell why a tool was
missing. `core/agent_permissions.py` is retained for its documented blast-radius sets
(`DANGEROUS`, `WRITE`), which are still the reference for what a destructive tool is.
Profiles are likewise backend-only now: the API and `/mcp/p/<name>` endpoints remain,
but the launch wizard and Settings no longer expose them. Public services (web search,
weather, Wikipedia, …) are ordinary connections — listed, and **off by default**, so an
agent reaches the internet only when deliberately ticked.

**Non-destructive updates.** `data/`, `config/`, and the mounted `.env` persist across
`docker compose pull && up -d`. The multi-stage Dockerfile builds `ui/web` and ships
only the built assets. See the v6.0.0 changelog.

The sections below describe the process model and subsystems in more depth; where they
mention `main.py` endpoints or the framework-free SPA, read the v6 map above as current.

---

## 1. Process model

Plutus runs **two servers in two processes** from a single `python main.py`:

```
 main.py (PID 1 in container)
 ├─ main process ──────────────►  MCP server      (uvicorn, port 8765, /mcp  streamable-HTTP)
 │                                 FastMCP app + MCPBearerGateMiddleware
 └─ daemon child (multiprocessing) ► Web UI server (uvicorn, port 8766, /ui  FastAPI)
```

- **MCP server** (`_run_mcp_main` → `mcp.streamable_http_app()`): the product surface.
  MCP clients (Claude Desktop/Code, Cursor, ChatGPT, n8n, …) connect here.
- **Web UI** (`run_ui`): a FastAPI app (`ui_app`) serving the dashboard, settings,
  health probes, and the connection/export APIs under HTTP Basic auth.

The split lets you run **MCP-only** (`UI_ENABLED=false`) to halve the memory footprint.

### Why two processes (and the one consequence)

uvicorn owns the event loop, so running both servers in one loop would couple their
lifecycles and blocking work. The trade-off: **each process imports `config.cfg`
independently**, so a `.env` change made by the UI process is not automatically seen
by the MCP process. This matters for exactly one thing — the bearer-auth gate — which
is why `MCPBearerGateMiddleware` reads the token/flag from `.env` at request time
(see [SECURITY.md](SECURITY.md#bearer-authentication)). All other settings take effect
on restart, which is the documented contract.

### Supervision & lifecycle

- `_wait_for_ui_start` blocks until the UI child answers `/server/health` (or times out).
- `_start_ui_watchdog` runs a daemon thread that `join()`s the UI child; if it dies
  unexpectedly the main process exits non-zero so Docker's `restart: unless-stopped`
  recycles the whole container (otherwise the dashboard could be down while the
  container still looks "up").
- `_install_signal_handlers` traps SIGTERM/SIGINT, terminates+joins the UI child, and
  an `atexit` hook does the same on any clean exit.
- `_sweep_stale_tmp` removes leftover `*.tmp` files (from interrupted atomic writes) on boot.

---

## 2. Module map

```
main.py              Bootstrap, dependency check, FastMCP wiring, UI app + all HTTP
                     endpoints, auth, process orchestration.
config.py            Central env-driven configuration (singleton `cfg`) + the
                     UI-writable env-key allowlist.
client.py            Shared async HTTP helpers (arr_get/post, _handle_error with
                     redaction, fmt_size).

core/                Cross-cutting logic, deliberately UI/transport-agnostic:
  env_store.py         Canonical .env reader/writer (atomic, validated, cfg-sync).
  path_guard.py        Boundary-aware path confinement for fs/SMB tools.
  redact.py            Secret masking for file content surfaced to the model.
  ssrf_guard.py        Outbound-URL screening for web_fetch.
  rate_limit.py        Per-client login lockout.
  mcp_bearer_middleware.py  Live bearer gate for the MCP transport.
  mcp_export.py        Builds downloadable client configs (Connection Manager).
  agent_runner.py      Headless Claude Code runner over Plutus's own MCP tools.
  schedule_store.py    Persistent CRUD + cron validation for scheduled jobs.
  scheduler.py         APScheduler runtime firing agent/tool schedules.
  health_regression.py Tool-health baseline diff + alerting.
  batch_health.py /
  smoke_service_tools.py    In-app verification (zero-arg batch + round-trips).
  result_status.py     Success/failure text classifier.
  tool_registry.py /
  tool_gate.py /
  capabilities.py      Tool catalogue, slicing, per-tool gating.
  dashboard_*.py       Dashboard payload + service health probing.
  discover_services.py /
  docker_wizard.py /
  wizard_scan.py       LAN/Docker auto-discovery for the setup wizard.
  observability.py     In-memory telemetry (route latency/status ring buffer).

tools/               One module per domain, each exposing `register_*_tools(mcp)`:
  media.py personal.py photos.py system.py comfyui.py utilities.py obsidian.py
  monitoring.py nextcloud.py infrastructure.py fal_tools.py public_apis_bulk.py
  ssh_smb.py

ui/spa_page.py       Serves the built React shell (served at /app).
ui/web/              React + Vite SPA source, built to ui/static/dist/.

tests/               Offline pytest suite (no network).
docs/                This documentation set.
```

---

## 3. Tool registration

Every tool module exposes `register_<domain>_tools(mcp)`. `main.py` calls each at
startup; each uses the FastMCP `@mcp.tool(...)` decorator with a pydantic input model
(`extra="forbid"` to reject unexpected fields) and `readOnlyHint`/`destructiveHint`
annotations that drive the safety model (see [TESTING.md](TESTING.md)). User-supplied
tools can be added without forking via `extensions/__init__.py::register(mcp)`.

A **tool gate** (`core/tool_gate.py`, persisted in `data/plutus_tool_gate.json`) and a
**tool slicer** let you shrink the manifest an MCP client sees — by section, by
individual tool, or by a free-text "intent" — without removing code.

---

## 4. Request lifecycles

**MCP tool call:** client → `/mcp` → `MCPBearerGateMiddleware` (live `.env` token check)
→ FastMCP routes to the tool → pydantic validation → tool calls the homelab service via
`client.py` helpers → text/markdown result returned to the client.

**Dashboard action:** browser → `ui_app` → `_csrf_origin_guard` (cross-site POST
rejection) → `verify_auth` (Basic auth + login lockout) → endpoint handler → `core/*`
logic or a direct tool invocation via `core/invoke_tool.py`.

---

## 5. Configuration & state

- **Configuration** comes from `.env`, loaded into the `cfg` singleton at import. The
  one writer is `core/env_store.py` (atomic temp-file + `os.replace`, key allowlist,
  newline rejection, in-process `cfg` sync). Both the UI endpoints and the SSH/SMB
  managers go through it.
- **Runtime state** lives in `data/` (health baseline, recent runs, beta tool cache,
  uploaded CA). It is bind-mounted (`./data:/app/data`) so it survives rebuilds and can
  be backed up. See [OPERATIONS.md](OPERATIONS.md).

---

## 6. Design principles

1. **Fail safe, not open** — tools return a clear "not configured" string instead of
   crashing; destructive Docker/SSH actions are gated off by default.
2. **Don't leak** — upstream error bodies, exception text, and secrets in files are
   redacted before they reach the model/transcript unless explicitly opted in.
3. **The network is not the only boundary** — auth, CSRF, SSRF, and path-confinement
   controls exist even though the intended deployment is LAN/Tailscale.
4. **Verifiable** — pure logic is unit-tested offline; live integrations are checked by
   an in-app smoke/health system.
5. **Pragmatic for one maintainer** — no enterprise scaffolding the project doesn't need.
