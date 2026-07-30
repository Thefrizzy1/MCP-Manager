# Workforce — design plan for the rooms builder

A plan, not an implementation. It exists so the build order is argued before code
gets written, and so the one blocking constraint is dealt with first rather than
discovered halfway through.

The idea: named **rooms**, each with its own MCP connections and schedule; **agents**
placed in rooms with roles (manager / researcher / developer); a manager that hands
work between them; and a visual surface where you can see who is working, who is
idle, and what each one is doing right now.

---

## 0. The blocker: the runner is serial

Everything visual in this feature implies several agents busy at once. Today that
is impossible:

- `agent_runner._current` is a single dict, and `run_agent` returns
  `"An agent run is already in progress."` if it is occupied
  ([core/agent_runner.py](../core/agent_runner.py))
- `ui.runtime._agent_queue` is drained by exactly one worker thread

So a room of four agents would animate as: one working, three resting, forever.
The sim would be honest but useless.

**This is the first thing to build, before any UI.** The natural concurrency unit
is the *provider account*: one in-flight run per account. That bounds parallelism
to something you actually own, respects each plan's rate limits, and gives the
canvas a truthful reason why an agent is resting ("waiting for the Work Max
account"). Concretely:

- replace `_current` with a dict keyed by account, `{account_key: RunState}`
- replace the single queue worker with one worker per linked account
- `run_agent` refuses only when *that account* is busy
- runs already record `provider`/`account_id`, so the plumbing exists

Estimated: the largest single piece of backend work in the whole feature, and the
one that makes everything after it possible.

> **Status: still the blocker, but rooms no longer die on it.** Seats run in
> order, and each one now *waits* for the runner slot instead of being refused by
> it (`agent_runner.wait_for_slot`, used by `POST /api/v1/rooms/{id}/run`). Before
> that, launching a room while the agent queue happened to be busy failed on the
> first seat with `"An agent run is already in progress."` — which reads like a
> broken room rather than a slot that was two seconds from free. Parallelism still
> needs the per-account rework above; sequential-but-reliable is the interim.

---

## 1. What to borrow, and from whom

| Source | What is worth taking |
|---|---|
| [CrewAI](https://medium.com/@gaddam.rahul.kumar/agentic-ai-orchestration-for-full-stack-developers-comparing-autogen-crewai-langflow-flowise-e0f917e3cd4e) | **role / goal / backstory** per agent. The clearest mental model in the space, and it maps exactly onto manager / researcher / developer. Adopt the three fields verbatim. |
| [Agno Builder](https://www.agnobuilder.com/) | Named **team modes**: *coordinator* (one delegates), *collaborator* (parallel), *router* (best agent takes it). Three modes cover essentially every useful topology. |
| [AutoGen Studio / Flowise](https://you.com/resources/popular-agentic-open-source-tools-2026) | Canvas as a *view over_ a declarative config, not the source of truth. Both export to code; the graph is a projection. |
| Contact-centre supervisor dashboards | The mature answer to "see who is busy". Hovering a status icon opens a popover naming the precise state — *"Chat: Engaged"*. This is exactly the requested hover behaviour, already solved. |
| [Ambient-agent UX patterns](https://www.bprigent.com/article/7-ux-patterns-for-human-oversight-in-ambient-ai-agents) | On entry the user's first question is *what is the state* — idle / running / paused — with an obvious kill switch. Status and override before anything decorative. |

### One thing deliberately **not** borrowed

A free-form node-and-edge canvas. Flowise-style graphs give enormous surface area
for a handful of useful shapes, and every edge becomes a thing that can be wired
wrong. Agno's finding is the tell: three modes were enough.

**Recommendation:** drag agents *into rooms* and reorder them, but let the room
declare its topology (coordinator / pipeline / parallel). You keep the tactile feel
and lose the failure modes of arbitrary graphs. If a genuinely custom topology is
needed later, the data model below can grow explicit handoff edges without a
rewrite.

---

## 2. Data model

Rooms are stored like profiles and schedules already are — a JSON file under
`data/`, validated on load.

```jsonc
// data/workforce.json
{
  "rooms": [{
    "id": "research",
    "label": "Research room",
    "topology": "coordinator",          // coordinator | pipeline | parallel
    "mcp_services": ["websearch", "obsidian", "nextcloud"],
    "output": { "mode": "obsidian", "folder": "research" },
    "schedule": { "cron": "0 7 * * *", "timezone": "Europe/Berlin", "enabled": true },
    "seats": [
      { "id": "mgr", "role": "manager",    "provider": "claude", "account_id": "work-max-…",
        "goal": "Turn the brief into concrete tasks and review what comes back",
        "backstory": "You run a small research desk." },
      { "id": "res", "role": "researcher", "provider": "gemini", "account_id": "personal-…",
        "model": "gemini-2.5-pro",
        "goal": "Gather sources and summarise findings" },
      { "id": "dev", "role": "developer",  "provider": "codex",  "account_id": "personal-…",
        "model": "gpt-5.1-codex",
        "goal": "Turn accepted findings into working code" }
    ]
  }]
}
```

Notes on the shape:

- **A room's `mcp_services` reuses the existing per-connection ACL** — the same list
  the launch wizard already sends, resolved by `_agent_service_disallow`. A room is
  therefore a *scope*, and the tool-exposure work already done applies unchanged.
- **A seat binds a role to a provider account.** That is where "Codex is a coding
  runtime, Gemini a research one" becomes real: the seat's role and the provider's
  `role` (already in `core/ai_providers.PROVIDERS`) should agree, and the UI should
  warn when they don't rather than forbid it.
- **`model` is per seat**, and optional (empty = the account's own default). It has
  to be: a room mixes providers, and `gpt-5.1-codex` means nothing to Gemini.
- **`output`** reuses `agent_runner.resolve_library`, so room results land in the
  same Obsidian/filesystem library as playbooks.

---

## 3. Execution model — no new runtime

A room run is a **sequence of ordinary agent runs** whose prompts are composed from
prior outputs. Nothing new is needed at the runtime layer:

```
scheduler fires room
  → build brief from the room's goal
  → for each step in the topology:
        render the seat's prompt (role + goal + backstory + prior outputs)
        enqueue via the existing _enqueue_agent(..., provider, account_id, mcp_services)
        await the run record; capture result
  → persist a RoomRun: {room_id, started, steps:[{seat_id, run_id, ok, cost}], total_cost}
```

Topologies are just iteration orders:

- **coordinator** — manager runs first to produce tasks, each worker runs on its
  task, manager runs last to review and assemble
- **pipeline** — seats in order, each receiving the previous output
- **parallel** — all workers on the same brief at once (requires §0), then a manager
  pass to merge

Because every step is a normal run, you inherit the whole existing surface for
free: transcripts, cost accounting, the daily cap, `auth:` provenance, the
connection ACL, and per-run "Run again".

**Cost is the risk.** A four-seat room on a daily cron is four runs per fire. The
existing `max_cost_usd` guard is per-run, so a room needs its own per-room and
per-fire budget, checked before each step. Build this with the feature, not after.

---

## 4. The visual surface

Two views over the same config, because they answer different questions.

### Builder (design time)

```
┌ Rooms ──────────────────┐  ┌ Research room ───────────────────────────┐
│ ▸ Research room      3  │  │ Topology  ( coordinator ▾ )              │
│ ▸ Build room         2  │  │ Schedule  daily 07:00 Europe/Berlin      │
│ ▸ Ops room           1  │  │ Connections  [ 3 of 58 ▾ ]               │
│ + New room              │  ├──────────────────────────────────────────┤
└─────────────────────────┘  │  ⬤ manager     Claude · Work Max         │
                             │  ⬤ researcher  Gemini · Personal    ⇅    │
┌ Unseated agents ────────┐  │  ⬤ developer   Codex  · Personal    ⇅    │
│ ⬤ researcher (Gemini)   │  │  ＋ add seat                             │
│ ⬤ developer  (Codex)    │  └──────────────────────────────────────────┘
└─────────────────────────┘
```

Drag from *Unseated agents* into a room; drag the `⇅` handle to reorder. Reordering
is meaningful for `pipeline`, ignored for the others — and the UI should say so
rather than silently doing nothing.

### Floor (run time)

The sim view. One tile per seat, grouped by room:

| State | Visual | Meaning |
|---|---|---|
| `idle` | dimmed, no pulse | nothing scheduled |
| `queued` | outline pulse | waiting on its account to free up |
| `working` | filled, animated | run in flight |
| `blocked` | amber | needs auth, or its account is unlinked |
| `failed` | red, sticky | last run failed; stays until acknowledged |

**Hover** opens a popover — the contact-centre pattern — with: current step, the
tool call in flight, elapsed time, cost so far, and the account in use. Sourced
from the live run's `LIVE["lines"]` plus the run record. Click opens the existing
transcript modal.

Deliberately: **no idle animation loop.** A tile that animates while nothing runs
teaches you to distrust the display. Motion should mean work.

---

## 5. Build order

Each slice is independently shippable and useful on its own.

1. **Concurrency (§0).** Per-account run slots. No UI. Unblocks everything, and on
   its own makes the current single-agent page better.
2. **Room config + storage.** `core/workforce.py`, JSON store, validation, CRUD API.
   No execution yet.
3. **Room execution.** Topology iteration over `_enqueue_agent`, `RoomRun` records,
   per-room budget guard. Drive it from an endpoint; no scheduler yet.
4. **Builder UI.** Room list, seat list, drag to seat, topology and schedule pickers.
   Reuses `ConnectionPicker` and the account picker as-is.
5. **Scheduling.** Rooms as a new `kind` in the existing `schedule_store`.
6. **Floor view.** Live states, hover popovers, links into transcripts.
7. **Handoff editing** — only if slices 1-6 prove the fixed topologies too rigid.

Slices 1-3 are the substance. 4-6 are presentation over a working engine, which is
the right order: an engine with no canvas is testable, a canvas with no engine is a
mockup.

---

## 6. Open questions

- **Human-in-the-loop.** Should a manager be able to pause and ask? The ambient-agent
  patterns argue strongly for an approval step on anything irreversible. That needs a
  run state the current runner has no concept of (`awaiting_input`).
- **Failure policy per room.** Does a failed researcher stop the room, or does the
  manager get told and continue? Needs to be a per-room setting; defaulting to "stop"
  is the safe start.
- **Context size.** A pipeline accumulates every prior output into the next prompt.
  Needs a summarisation step or a hard cap, or step four of a long room blows its
  context window.
- **Do rooms replace playbooks?** They overlap. A single-seat room *is* a playbook.
  Worth deciding before both exist and drift.
