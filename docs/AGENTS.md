# Plutus — Agents & Scheduler

Plutus can run a **headless Claude Code agent** that operates its own ~193 MCP
tools, and can **schedule** agent tasks or individual tool calls on a cron. Open
the dashboard → footer → **Agents**.

---

## 1. What it is

- **Agent runner** (`core/agent_runner.py`) spawns `claude -p --output-format
  stream-json` as a subprocess and hands it Plutus's own MCP endpoint via a
  generated `--mcp-config`. So a single prompt like *"check which \*arr queues are
  stuck and restart any unhealthy container"* can read and act across your whole
  homelab. A live console (SSE) streams the agent's steps; each run is saved with
  cost and turn count.
- **Scheduler** (`core/scheduler.py` + `core/schedule_store.py`) fires two kinds
  of jobs on a cron expression:
  - **agent** — a saved prompt, run headless.
  - **tool** — any Plutus MCP tool with fixed params (e.g. `sonarr_queue`, or a
    notification tool).

State lives in `data/agent_runs/`, `data/agent_config.json`, and
`data/schedules.json` (all under the persisted `./data` volume).

---

## 2. One-time setup (container)

The pre-built image already includes Node.js and Claude Code. You only need to
authenticate the agent once:

**Option A — interactive login (uses your Claude plan's automated-usage credit):**

```bash
docker exec -it plutus-mcp claude      # follow the OAuth link once
```

The login persists because `~/.claude` is mounted in `docker-compose.yml`.

**Option B — API key (bills per token):** set `ANTHROPIC_API_KEY=sk-ant-…` in the
compose `environment:` block.

If neither is set, agent runs return *"claude not found / not logged in"* and
nothing else breaks.

> **Cost:** agent runs spend real credit/tokens. `data/agent_config.json` has a
> `max_cost_usd` guard (flags over-budget runs) and a `timeout_min`. Keep
> scheduled prompts modest.

---

## 3. Configuration

`data/agent_config.json` (editable via `POST /api/v1/agent/config`):

| Key | Default | Meaning |
|---|---|---|
| `model` | `""` | Claude model (empty = Claude Code default) |
| `allowed_tools` | `["mcp__plutus","Read","Write","WebSearch","WebFetch"]` | Tools the agent may use (`mcp__plutus` = all Plutus tools) |
| `give_plutus_tools` | `true` | Write the `--mcp-config` pointing at Plutus's MCP |
| `skip_permissions` | `true` | Headless `--dangerously-skip-permissions` |
| `timeout_min` | `20` | Per-run wall-clock cap |
| `max_cost_usd` | `2.0` | Over-budget flag threshold |
| `library` | `"research"` | `{{LIBRARY}}` folder playbooks read/write (Obsidian folder or path) |

The agent reaches Plutus at `http://127.0.0.1:8765/mcp` (same container); if
`MCP_REQUIRE_BEARER=true`, the bearer token is injected automatically.

---

## 4. Playbooks & the knowledge library (the "gets smarter" loop)

**Playbooks** are named, editable research tasks (`data/agent_tasks.json`, seeded
with starters on first run). Each is a prompt for the agent; you can **Run** one
now or **Schedule** it. Starters target the @the_frizzy1 channel:

| Playbook | Does |
|---|---|
| Competitor research | Deep-researches comparable AI/ComfyUI YouTube channels, logs what's working |
| AI & ComfyUI trend scan | Finds new models/nodes/workflows (low-VRAM biased) |
| My channel audit | Audits recent videos against your standards, lists fixes |
| Script draft from research | Turns the best findings into a script in your voice |
| Weekly digest | Summarizes the week's research into one brief |

**The compounding part:** every research playbook is instructed to **read the
existing library first, then add or refine notes.** The library is a folder the
agent reads and writes via Plutus's own Obsidian/filesystem tools — set it in the
Agents panel (**Knowledge library folder**, default `research`; point it at an
Obsidian folder in your vault). Because each run stands on the last, the notes get
richer and the scripts/opportunities get sharper over time.

Placeholders in a playbook prompt: `{{LIBRARY}}` (your library folder) and
`{{DATE}}` (today) are filled in at run time.

> **"Check my numbers":** basic channel/competitor research works from public web
> data. Real analytics (CTR, retention, impressions) need the YouTube Data/Analytics
> API — add a YouTube MCP server to `allowed_tools` when you want that (phase 2).

Schedule kinds: `agent` (ad-hoc prompt), `task` (a playbook by id — edits to the
playbook flow through), or `tool` (a single Plutus tool call).

## 5. Scheduling

**Cron** is standard 5-field (`min hour day month weekday`), with a timezone.
Examples: `0 3 * * *` (daily 03:00), `*/30 * * * *` (every 30 min),
`0 8 * * 1` (Mondays 08:00).

- Add/remove schedules in the Agents panel, or via the API (below).
- A misfired job (container was down) coalesces and runs once with a 1h grace.
- **Run now** fires a schedule out-of-band immediately.

Scheduling requires `apscheduler` (in the image). Without it, the store and UI
still work but jobs don't fire — the panel shows a clear warning.

---

## 6. API

All under the dashboard's Basic auth.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/agent/status` | Running state, total spend, availability |
| POST | `/api/v1/agent/run` | `{prompt, label}` — launch a run |
| GET | `/api/v1/agent/stream` | SSE live console |
| GET | `/api/v1/agent/runs` | Recent run records |
| POST | `/api/v1/agent/config` | Update agent config (incl. `library`) |
| GET | `/api/v1/agent/tasks` | List playbooks (seeds starters) |
| POST | `/api/v1/agent/tasks` | Create/update a playbook |
| DELETE | `/api/v1/agent/tasks/{id}` | Delete a playbook |
| POST | `/api/v1/agent/tasks/{id}/run` | Run a playbook now |
| GET | `/api/v1/schedules` | List schedules + next-run times |
| POST | `/api/v1/schedules` | Add `{name,kind,cron,timezone,payload}` |
| POST | `/api/v1/schedules/{id}` | Update |
| DELETE | `/api/v1/schedules/{id}` | Remove |
| POST | `/api/v1/schedules/{id}/run-now` | Fire immediately |

`payload` is `{prompt}` for `kind:"agent"` or `{tool, params}` for `kind:"tool"`.

---

## 7. Security

The agent runs with `--dangerously-skip-permissions` and can drive real tools, so
treat the dashboard credential as powerful. Keep the box on LAN/Tailscale, set a
strong `UI_PASSWORD`, and keep destructive tool gates (`DOCKER_WRITE_ENABLED`,
SSH read-only) as their safe defaults unless a scheduled task genuinely needs
them. See [SECURITY.md](SECURITY.md).
