# Plutus — Remaining Work (handoff brief)

You are picking up an in-place architecture rebuild of the **Plutus MCP Manager**
on the `main` branch. The audit and its per-finding status live in
[ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) — **read it first.** Every safe,
clear-cut finding has already been implemented, tested, and pushed. What remains
below is deliberately deferred: each item is either a **product decision** (the
maintainer must sign off on a behavior change) or a **high-risk / marginal-value**
change. Do them one at a time, only when chosen.

---

## How work is done in this repo (conventions — follow exactly)

- **Branch:** commit directly to `main`. The Docker deploy pulls `main`, and CI
  publishes the image on every green run. There is no PR flow.
- **Increments:** one logical change per commit, each committed **and pushed**
  separately, with the offline suite green first. End commit messages with the
  `Co-Authored-By` trailer the other commits use.
- **Tests (the gate):** `python -m pytest -q -m "not live"` must stay green
  (currently **1086 passed, 0 failed**, ~75s). The `live` tests hit third-party
  APIs and are informational only — never gate on them. CI runs the same
  `-m "not live"` gate plus an informational live-smoke step.
- **Docs are tested too.** `tests/test_docs_and_layering.py` fails if a doc names
  a source file that does not exist, if `ARCHITECTURE.md`'s module map names a
  module that is gone or omits a `core/` module, or if anything in `tools/`
  imports `ui.*`. Add a module → add it to the map in the same commit.
- **Frontend:** React + Vite in `ui/web/` → built to `ui/static/dist/` (gitignored;
  CI/Docker builds it). After any `ui/web/src` change run
  `npm --prefix ui/web run build` (this is `tsc -b && vite build`) and confirm it
  passes. Commit source only, never `dist/`.
- **Never touch** `.env` or `data/` (gitignored runtime state / secrets). Tests
  must use `tmp_path`, never the real `data/`.
- **Verify claims.** Run the suite; if a change is observable, boot the app
  (`python main.py`, MCP :8765 + UI :8766) and probe it before claiming it works.
  A known-flaky-free suite is the bar.

## Key architecture facts a cold session needs

- **Two processes** from `python main.py`: the MCP tool server (`:8765`, the
  product) and the Web UI (`:8766`, a `multiprocessing` child). Each imports
  `config.cfg` independently. `core/live_config` refreshes the MCP process's cfg
  from `.env` on the request path so UI edits reach the tools without a restart.
- **Agent gating has two axes:** *write/publish* (`core/agent_permissions.capability_disallow`)
  and *connections* (`core/agent_orchestrator.service_disallow`). The old
  `strict_read/safe/all` permission-*level* model was removed.
- **Agent execution engine:** `core/agent_orchestrator.py` (queue, worker, run
  invocation, mcp_target). `tools/` must never import `ui.*`.
- **Service metadata:** canonical in `core/builtin_services.py` (`SERVICES`,
  `SERVICE_LOGO_DOMAIN`, `SERVICE_ICON_SLUG`, `OPEN_URL_BY_ID`); typed view in
  `core/service_defs.py`; `config.py` holds the pydantic `cfg` fields. Drift
  between config.py and SERVICES is guarded by `tests/test_service_defs.py`.
- **Manifest slicing:** `core/tool_exposure.py` (categories) + `core/profiles.py`
  (per-endpoint subsets). A fresh install seeds a lean default (novelty categories
  off) via `ensure_exposure_seed`.

---

## Remaining items

### 1. Full dynamic `config.py` generation (D#2) — HIGH RISK, low real payoff
**Goal:** generate the ~111 per-service pydantic fields on `config.Config` from
`core/service_defs.py` instead of hand-declaring each `foo_url: str = _get("FOO_URL")`.

**Why deferred:** `cfg.<svc>_url` / `cfg.<svc>_api_key` are read across the whole
codebase; a single missed or mis-typed field silently breaks that service. The
drift-guard in `tests/test_service_defs.py` already makes the current duplication
**safe**, so the remaining payoff is mostly cosmetic.

**If you do it:**
- Keep every existing `cfg.<attr>` access working identically. Preserve:
  `Config.model_fields` (used by `config.apply_live_env` + `_cfg_attr_for`), the
  `_get()` trailing-slash strip for `_URL` keys, `_ATTR_OVERRIDES`
  (`SSH_HOSTS`→`ssh_hosts_json`, `SMB_SHARES`→`smb_shares_json`), and field types
  (str/bool/int/list) with their defaults.
- The **globals** (`mcp_host`, ports, `ui_*`, `filesystem_allowed_paths`,
  `mcp_*`, `forwarded_allow_ips`, …) and the **dashboard-bookmark** URLs
  (`audiobookshelf_url`, `kavita_url`, … — not in `SERVICES`) are not services;
  keep them hand-declared or add an explicit globals table to the registry.
- Approach: build the model with `pydantic.create_model` (or a computed
  `model_config`) from `service_defs()` + globals. Add a test asserting the
  generated field set + types + defaults exactly match a snapshot of the current
  hand-written ones. Boot the app and probe a couple of services.
- **Recommendation:** likely *not worth it*. Consider marking DONE-BY-DECISION
  (the drift guard is the pragmatic SSOT) unless maintainability is the priority.

### 2. Tool-description compaction / near-duplicate dedupe (A#4) — PRODUCT DECISION
**Goal:** cut per-request manifest tokens further by trimming verbose tool
descriptions and/or collapsing near-duplicate tools.

**Tradeoff:** descriptions are what agents read to choose tools; over-trimming
hurts tool-selection accuracy. Collapsing tools (e.g. `github_*`/`gitlab_*`
mirrors, the currency/crypto overlaps) changes the tool surface. **Get the
maintainer's sign-off on how aggressive to be.**

**How:**
- Measure first: `core/tool_exposure.exposure_report(root, tool_manager)` gives
  per-tool token estimates; find the longest descriptions.
- Prefer **description trim over dedupe.** Keep each tool's discriminating
  "when to use this" signal; cut boilerplate/marketing prose. Descriptions live in
  the `@mcp.tool(...)` docstrings in `tools/*.py` (the 58 `pub_*` are in
  `tools/public_apis_bulk.py`, already lean-defaulted off).
- Verify: token estimate drops; `pytest -m "not live"` green; ideally run a real
  agent and confirm it still picks the right tools.

### 3. Unify the 3 probe pipelines (C#5) — big refactor, marginal now
`core/smoke_service_tools.py` (mutation round-trips), `core/batch_health.py`
(zero-param calls), `core/dashboard_health.py` (HTTP probes). They serve genuinely
different jobs. The *classifier* flakiness that motivated this is already fixed
(`text_looks_successful` and `looks_like_missing_service_config` are head-anchored;
the batch is concurrent). If you touch it, the useful bit is extracting one shared
`classify_tool_output(text) -> pass|unset|fail` used by `batch_health` (which
`health_regression` already consumes) — not merging the transports. **Low value.**

### 4. Playbooks in narrow profiles (A#8) — PRODUCT DECISION, not a bug
Today a `/mcp/p/<name>` profile exposes **no** playbooks/prompts: `tools/prompts.py`
filters prompts by the *tool* allow-set against *playbook ids*, so a filtered
profile matches none. This is intentional-ish (playbooks live on the full `/mcp`).
**Decision:** should narrow profiles include playbooks? If yes, either register all
prompts regardless of the tool `allow` filter (prompts are cheap, fetched on
demand), or add a separate prompt-allowlist to the profile schema
(`core/profiles.py`). Ask before changing.

### 5. `resources.py` ignores `allow` (A#7) — Low
`tools/resources.py` registers all four `plutus://` resources on every profile/
slice, and `plutus://health` does a live gather on read. Gate them on non-empty
profiles, or confirm the current always-on behavior is intended.

### Doc drift — audited 2026-08-03, now guarded
An audit found this section had been too optimistic: `ARCHITECTURE.md` was *not*
already correct. It documented the since-deleted `tool_gate` module as live,
claimed a run is governed "solely" by connections (there are two axes —
connections and write/publish), and its module map listed 25 of 62 `core/`
modules. All three are
fixed, and `tests/test_docs_and_layering.py` now fails on each class of error, so
this cannot silently recur.

`docs/CHANGELOG.md` and `docs/AGENT_AUDIT.md` still mention the retired
`strict_read/safe/all` levels and are **deliberately exempt** from the guard:
both record a past moment rather than the current system, and an audit's
recommendation to create a file is not a claim that it exists.

`docs/TESTING.md` names 14 of 71 test files. Not wrong, just partial — it reads
as a guide rather than an index, so this is left as-is.

---

## Paste-able kickoff prompt for a fresh Claude Code session

> Continue the in-place architecture rebuild of the Plutus MCP Manager on `main`.
> Read `docs/REMAINING_WORK.md` and `docs/ARCHITECTURE_AUDIT.md` first — the safe
> audit items are all done; only the deferred items in REMAINING_WORK remain, and
> each is a product decision or high-risk/marginal change. Follow that file's
> conventions exactly: commit+push each increment to `main` separately, keep
> `python -m pytest -q -m "not live"` green (currently 1086 passing), run
> `npm --prefix ui/web run build` after any frontend change, and never touch
> `.env`/`data/`. **Do not start item 1 (full config.py generation) or item 2
> (description compaction) without my explicit go-ahead — they change behavior.**
> Tell me which item you recommend and why, then wait for my pick.
