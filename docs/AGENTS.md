# Plutus — Agents & Scheduler

Plutus can run a **headless Claude Code agent** that operates its own ~193 MCP
tools, and can **schedule** agent tasks or individual tool calls on a cron. It has
a dedicated workspace in the SPA at **`/app#/agents`** (sidebar → **Agents**; the
old `/agents` URL redirects there). Key areas:

- **Run** — one-off prompt with a live console.
- **Playbooks** — the library of research tasks, plus **Build an agent with Claude**:
  describe what you want in plain language and the running Claude Code drafts a
  playbook prompt you can edit and save.
- **Schedules** — cron a playbook, an ad-hoc prompt, or a single tool call.
- **Settings** — model, tools, timeout, cost guard, **where output goes** (Obsidian
  vault folder or a filesystem path), and ntfy notifications.
- **History** — every run with cost and output, expandable.

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

The image includes Node.js and Claude Code. Authenticate the agent once — and the
method you choose decides how it's billed. **All the recommended paths use a
session/OAuth token from your Claude subscription — never an API key.**

**Option A0 — web login from the dashboard (no container shell, recommended):**
Agents page → **Settings** → *Connect Claude account*. On any machine signed into
your Claude account, run `claude setup-token` in a terminal (it opens your browser,
you approve, and it prints a token); paste that token into the dashboard and Save.
Plutus stores it as `CLAUDE_CODE_OAUTH_TOKEN` and the agent uses it immediately (no
restart, no `docker exec`).

> There's intentionally no "click to log in via browser inside the dashboard": the
> container has no browser and the OAuth localhost callback isn't reachable, so that
> flow can't work. Running `setup-token` on your own machine and pasting the token is
> the supported path.

**Option A — terminal login with your Claude subscription:**

```bash
docker exec -it plutus-mcp claude      # follow the OAuth link once, with your Pro/Max account
```

`claude -p` then draws from your **plan's Claude Code usage** — the same allowance
you use interactively — so scheduling playbooks overnight uses the usage window
you're not touching during the day. The login persists via the `~/.claude` mount.
**Do not set `ANTHROPIC_API_KEY`** in this mode.

**Option B — API key (pay-per-token):** set `ANTHROPIC_API_KEY=sk-ant-…` in the
compose `environment:` block. This **overrides** the login and bills the Anthropic
API per token. Only use it if you specifically want that.

The Agents panel shows which mode is active (green *"using your Claude plan"* vs a
warning). If neither is set, runs return *"claude not found / not logged in"* and
nothing else breaks.

> **Note on "usage I don't use at night":** the plan's limit is a rolling window /
> weekly cap, not banked per-session credit — but running agents while you're asleep
> means that shared allowance is free for them. Keep the `max_cost_usd`/`timeout_min`
> guards sensible so a job can't run away with your window.

> **Cost:** agent runs spend real credit/tokens. `data/agent_config.json` has a
> `max_cost_usd` guard (flags over-budget runs) and a `timeout_min`. Keep
> scheduled prompts modest.

---

## 2b. Other providers (Codex, Gemini)

Everything above is the **Claude** runtime, still the default. Settings → **AI
providers** adds accounts on two more, and the launch wizard's **Account** picker
chooses which one executes a run (`core/ai_providers.py`).

| Provider | Kind | Credential | How it reaches Plutus's tools |
|---|---|---|---|
| Claude Code | CLI (`claude -p`) | login in the account's `CLAUDE_CONFIG_DIR`, or a `setup-token` | `--mcp-config` |
| Codex | CLI (`codex exec`) | login in the account's `CODEX_HOME` | stdio bridge in `config.toml` |
| Gemini | HTTP API | free key from <https://aistudio.google.com/apikey> | function calling |

The runtime follows the **account**, not a setting: pick a Codex account and
`codex exec` is what runs. Getting that wrong was a real bug — the runner built a
`claude` command whatever you picked, so a Codex run was Claude Code pointed at a
Codex config directory, and it failed as `401 Invalid bearer token`.

**Gemini needs no CLI and no login.** Add an account, paste an AI Studio key, done.
It used to drive `@google/gemini-cli`, which has no config-dir override — it always
reads `~/.gemini` — so a second account meant logging in, "adopting" the credential
file, logging out and logging in again as the other identity. The key does the same
job in one paste, isolates accounts by construction, and is why the image no longer
ships that CLI.

**Models are per provider and per run.** The wizard's Model menu is populated from
whichever account you picked — Claude's Opus/Sonnet/Haiku, Codex's GPT ids, and for
Gemini the list its API actually reports for your key. Any menu also accepts a typed
id, because vendors add and drop models between releases. The choice rides with the
run; it is no longer written into `data/agent_config.json`, where one Opus launch
used to pin Opus on every later run including ones on other providers.

### All three runtimes get the same ~209 tools

Three roads, one destination — every provider ends at Plutus's own MCP endpoint,
so an agent's abilities do not depend on which account happens to run it.

- **Claude** — the generated `--mcp-config`, unchanged.
- **Codex** — `codex exec` has no `--mcp-config`, but it reads
  `$CODEX_HOME/config.toml`, and every account already has its own `CODEX_HOME`.
  Before each run Plutus writes an `[mcp_servers.plutus]` block there pointing at
  **`tools/mcp_stdio_bridge.py`**, a stdio MCP server that relays to the HTTP
  endpoint. Only that block is rewritten; anything else in the file survives.

  Why a bridge instead of giving Codex the URL: stdio (`command`/`args`/`env`) is
  the one transport shape every Codex release has accepted. HTTP support has moved
  between an experimental flag and different config keys across versions, and a key
  the installed Codex rejects fails the *whole run*, not just the tool wiring.

- **Gemini** — no MCP support at all, so the equivalent is its **function calling**
  loop (`core/agent_runner._execute_api`). Plutus reads its own `tools/list`,
  converts each JSON Schema into Gemini's stricter OpenAPI subset
  (`core/agent_tools.py`), and every time the model asks for a tool it is really
  called and the result fed back. Up to `MAX_TOOL_TURNS` rounds per run.

  The conversion is not optional tidying: Plutus's schemas are Pydantic-generated
  (`$ref` into `$defs`, `anyOf` with a null branch, `default`/`title`/`minimum`),
  and Gemini validates the entire request — one stray keyword and *every* tool call
  in the run fails. `tests/test_agent_tools.py` converts the whole live registry and
  asserts the result stays inside the accepted dialect.

  Gemini forbids mixing function declarations with built-in tools, so a run with
  Plutus tools does not also get Google Search grounding. Nothing is lost: Plutus's
  own web-search tools are in the set. A run with tools switched off still gets
  grounding.

### Where the work lands: the research library

`data/library` inside the app — created on demand, on the volume that is already
persisted, browsable in **Files**, and downloadable (a file, or a whole folder as
one zip).

It is writable by the filesystem tools *without* being added to
`FILESYSTEM_ALLOWED_PATHS`. That allowlist gates access to the **host**, and this
is app storage, not the host — confinement is still the same boundary-aware check,
it just has one more root. Before this, the only writable paths were the operator's
own mounts, and the configured library default was the host path `/data/library`
that exists on nobody's install, so an agent asked to write something up answered —
truthfully — that it had no way to create a file.

So an agent can be told to research a topic and build a structure: subfolders,
Markdown notes, an HTML dashboard. `fs_write_file` creates parent directories, so
it only needs the path.

**Three of those tools are built into the runner, not fetched over MCP** —
`library_write_file`, `library_read_file`, `library_list_files`
(`core/agent_tools.py`). Gemini gets them as function declarations; Codex gets
them injected by the stdio bridge, which executes them in its own process. They
take paths *relative* to the library, so confinement is a property of the API
rather than something each caller re-checks.

They are built in because being able to write up the work must not depend on what
an endpoint happens to expose. A run against a profile serving a read-only slice
ended with the agent reporting it had "no tool available to create or upload
files" and asking the user to make the file by hand. Now the homelab tools can be
missing, restricted, or the whole MCP endpoint unreachable, and the agent can
still produce output — that case is covered by a test that points the bridge at a
dead port and writes a file anyway. The connection ACL can still deny them
explicitly; an operator who does that means it.

**Models rot; ids are not pinned.** Google retires a model id for *new* keys
("This model models/gemini-2.5-flash is no longer available to new users") while
the account that has always used it keeps working. So Gemini's model is resolved
against the account's own live list (`ai_providers.resolve_model`) rather than a
constant, the shipped fallbacks are `-latest` aliases, and Settings → Test no
longer names a fixed model — it asks the key what it can reach.

**Scope still comes from the connection picker.** Claude gets `--disallowedTools`;
`codex exec` has no equivalent, so the bridge enforces it — denied tools are
removed from `tools/list` and refused on `tools/call`, which is stricter than a CLI
flag because the model is never even told they exist. Gemini simply never has them
declared. Gemini also caps declarations at `agent_tools.MAX_DECLARATIONS`; past
that the run log names how many were left out, and narrowing connections is the fix.

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
| `tool_permission` | `"safe"` | Blast-radius control: `strict_read` / `safe` / `all` (see below) |
| `max_runs_per_day` | `20` | Scheduled/queued runs are refused past this (manual runs override) |
| `output_mode` | `"obsidian"` | Where the library lives: `obsidian` or `filesystem` |
| `obsidian_folder` | `"research"` | Vault-relative folder when `output_mode=obsidian` |
| `fs_library_path` | `"/data/library"` | Host-mounted path when `output_mode=filesystem` (must be in `FILESYSTEM_ALLOWED_PATHS`) |
| `notify_enabled` / `notify_on` | `false` / `all` | Optional ntfy after each run (`all` or `error`) |

All of these are configured from the **Agents** workspace in the SPA
(`/app#/agents`, or the redirecting `/agents`).

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

## 4b. Tool permissions (blast-radius control)

The agent runs headless (skip-permissions) and can reach Plutus's tools while reading
untrusted web pages — a prompt-injection route to destructive actions. A permission
level (Settings → *Tool permission*, or per-playbook) decides what it may touch:

| Level | The agent can… |
|---|---|
| `strict_read` | read only — no writes at all (pure audits) |
| `safe` *(default)* | read **and** write notes to your library, but **not** infrastructure/irreversible tools (docker stop/restart, deletes, `ssh_run`/`ssh_exec`, HA control, `send_email`, torrent delete, `n8n_trigger_webhook`, image gen) |
| `all` | everything — full access |

Enforced via Claude Code `--disallowedTools`. Per-playbook `permission` overrides the
global default. Server-side gates (`DOCKER_WRITE_ENABLED` off, SSH read-only) remain the
backstop. See [SECURITY.md](SECURITY.md).

Other controls: a **Stop** button cancels the running agent; the Run tab shows
**runs today / daily cap** and **queue depth**; **Preview** shows a playbook's fully
rendered prompt before running; runs are **queued** (not rejected) when one is active.

## 5. Scheduling

You don't have to write cron: the schedule form has a **preset builder** (Daily at a
time / Hourly / Every N minutes / Weekly on a day) that fills the cron for you. Raw
cron stays available as "Custom" and is standard 5-field
(`min hour day month weekday`): `0 3 * * *` (daily 03:00), `*/30 * * * *` (every 30
min), `0 8 * * 1` (Mondays 08:00).

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
| POST | `/api/v1/agent/tasks/build` | `{description}` — Claude drafts a playbook prompt |
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
