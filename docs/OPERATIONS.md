# Plutus — Operations Runbook

Day-2 operations: deploying, health, shutdown, state/backup, monitoring, and
troubleshooting.

---

## 1. Deploy

```bash
cp .env.example .env          # then fill in service URLs/keys
chmod 600 .env                # secrets live here
# edit docker-compose.yml: set the NAS volume path/UUID for your host
docker compose up -d
docker compose logs -f plutus-mcp
```

Open the dashboard at `http://<host>:8766/ui` (Basic auth — set `UI_PASSWORD`).
The MCP endpoint is `http://<host>:8765/mcp`.

**MCP-only mode (lower RAM):** set `UI_ENABLED=false`. The dashboard is not served; the
healthcheck skips cleanly (see §3).

---

## 2. Process & lifecycle

`python main.py` runs the MCP server in the main process and the Web UI in a daemon
child process (see [ARCHITECTURE.md](ARCHITECTURE.md#1-process-model)).

- **Startup:** the parent waits for the UI child to answer `/server/health`, then
  starts a watchdog thread.
- **UI child dies unexpectedly:** the watchdog exits the main process non-zero so
  `restart: unless-stopped` recycles the container (the dashboard never silently stays
  down while the container looks "up").
- **Shutdown:** SIGTERM/SIGINT (e.g. `docker stop`) is trapped — the UI child is
  terminated and joined, and an `atexit` hook backstops cleanup.
- **Crash recovery:** stale `*.tmp` files from interrupted atomic writes are swept on boot.

---

## 3. Health & monitoring

### Container healthcheck

The Docker `HEALTHCHECK` (in both `Dockerfile` and `docker-compose.yml`) probes
`http://localhost:8766/server/health`. That endpoint **also TCP-probes the MCP port**
and returns **503 if MCP is unreachable** — so "healthy" means *both* halves are up, not
just the UI answering. In MCP-only mode (`UI_ENABLED=false`) the check is skipped so it
doesn't flap red forever.

```bash
docker inspect --format '{{.State.Health.Status}}' plutus-mcp
curl -s http://<host>:8766/server/health | jq        # {status, version, mcp_alive, uptime_s, ...}
```

### Scheduled tool-health regression alerts

`POST /api/v1/health/regression-check` runs the zero-arg tool batch, diffs it against a
saved baseline (`data/health_baseline.json`), and reports tools that **worked before and
fail now**. Query params: `?notify=1` pushes an ntfy alert on regressions; `?dry=1`
checks without updating the baseline.

The baseline never erases a known-good entry with a failure, so a regression keeps
alerting on every run **until it actually recovers** (no "one alert then silence").

Schedule it (same Basic auth as the UI):

```bash
# cron — daily 07:00, alert via ntfy on regressions
0 7 * * *  curl -s -u admin:$UI_PASSWORD -X POST \
  "http://<host>:8766/api/v1/health/regression-check?notify=1" >/dev/null
```

Or use an n8n Schedule → HTTP Request node. Use a dedicated/limited credential for
automation rather than your interactive admin password where possible.

### In-app verification

The dashboard's **Full check** button (and the `test_all_tools` MCP tool) run every
configured tool with safe inputs and report pass/fail. See [TESTING.md](TESTING.md).

### Telemetry

`GET /api/v1/observability` returns an in-memory snapshot of recent route
latency/status. It is **ephemeral** (a ring buffer lost on restart) — by design; there
is no on-disk request log to rotate.

---

## 4. State & backup

Runtime state lives in `data/` and is bind-mounted (`./data:/app/data`) so it survives
`docker compose down` / rebuilds:

| File | Contents | Sensitivity |
|---|---|---|
| `health_baseline.json` | Last-known tool pass/fail baseline | Low |
| `recent.json` | Recent tool runs (names + timestamps) | Low |
| `beta_tool_cache_*.json` | Cached smoke outputs + prefs | Low–Medium |
| `ca.pem` | Uploaded CA bundle (if any) | Medium |
| `plutus_tool_gate.json` | Tool exposure/slicer settings | Low |

`.env` (host file, not in `data/`) holds all secrets. Back both up:

```bash
tar czf plutus-backup-$(date +%F).tgz .env data/
```

Restore by placing `.env` and `data/` back and `docker compose up -d`.

---

## 5. Configuration changes

All config is in `.env`. Most changes require a **restart** to take effect
(`docker compose restart plutus-mcp`) because each process snapshots config at import.
The one exception is **bearer auth** (`MCP_REQUIRE_BEARER` / `MCP_BEARER_TOKEN`), which
the MCP gate reads live. See [CONFIGURATION.md](CONFIGURATION.md).

---

## 6. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `/ui` returns 503 | `UI_PASSWORD` unset | Set it in `.env`, restart |
| 429 on login | Login lockout after repeated failures | Wait out `Retry-After`; check for a misconfigured client |
| 403 "Cross-origin … (CSRF)" | Browser request from an unexpected Origin / proxy host | Confirm you're on the dashboard's own URL; if a proxy false-positives, set `PLUTUS_DISABLE_CSRF=1` |
| Tool: "Cannot connect to X" | Service down or URL unreachable **from the container** (e.g. `localhost` instead of LAN IP) | `docker exec plutus-mcp curl -I <URL>`; fix the URL/port |
| Tool: "non-JSON response from .../api" | Wrong endpoint or rejected credentials (e.g. OMV) | Verify URL + credentials; check service version |
| Container stuck "unhealthy" | MCP unreachable, or you're in MCP-only mode with an old healthcheck | Check `/server/health`; ensure `UI_ENABLED` is set correctly |
| Errors are vague | Error redaction is on by default | Set `PLUTUS_VERBOSE_ERRORS=1` temporarily to see upstream detail |
| Secret values show as `***` in `fs_read_file` | Redaction is on by default | Pass `reveal_secrets=true` if you truly need the raw value |
