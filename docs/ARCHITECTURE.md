# Plutus — Architecture

Plutus is a single self-hosted **Model Context Protocol (MCP) server** for a homelab,
paired with a **web dashboard** for configuration, health, and discovery. One process
tree exposes ~193 tools spanning media (Jellyfin, *arr, qBittorrent), photos (Immich),
home automation (Home Assistant), productivity (Nextcloud, Habitica, Obsidian),
infrastructure (Docker, OMV, SSH, SMB, Syncthing), and a large catalogue of public
APIs (weather, maps, search, finance, trivia).

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

ui/render.py         Builds the dashboard HTML.
ui/static/           dashboard.css, dashboard.js.

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
