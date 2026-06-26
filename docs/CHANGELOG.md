# Changelog

Notable changes to Plutus. Dates are approximate; this project is single-maintainer.

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
