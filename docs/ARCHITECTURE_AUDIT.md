# Plutus — Architecture Audit & Rebuild Roadmap

_Audit date: 2026-08-02. Method: full read of the backend by the maintainer's
Claude session plus four parallel read-only sub-agent audits (MCP tool system,
agent-execution subsystem, smoke/health reliability, config/registry/state).
Goal: redesign the architecture **in place** — same features, same UX — fixing
boundaries, coupling, data flow, and consistency introduced by rapid iteration._

This document is the single source of truth for the rebuild. Each finding has a
status: `TODO`, `WIP`, `DONE`, or `DEFERRED (reason)`. Keep it updated as work lands.

---

## 0. Baseline (must stay green)

- Test suite: **994 passing, 0 failing, 16 skipped** offline (`pytest -m "not live"`).
  This is the parity reference; no refactor may regress it.
- **Login fix (DONE):** `.env` had no `UI_PASSWORD`/`UI_USERNAME`; commit `b9115b5`
  removed the shared default, so first boot mints a random password printed once.
  A stale password → 401 → the browser Basic dialog re-prompts forever (there is no
  login *page* today). Product decision: default `admin`/`adminadmin` + a persistent
  "change the default password" banner, plus a real login page + multi-user (see §7).
- **Nextcloud calendars (DONE):** completed the in-progress writable-calendar work —
  `list_calendars` no longer re-sorts away the writable-first order and unpacks the
  4-tuple correctly (was raising `ValueError`); `add_event` now surfaces the
  read-only/fallback warning and writes to the resolved calendar.

---

## 1. Cross-cutting themes (the real architecture problems)

**A. Two-process split-brain — the root correctness flaw.**
The MCP tool server (main process) and the Web UI (`multiprocessing.Process`) each
import `config.cfg` independently and each use `threading.Lock`. Consequences:
`apply_live_env` updates only the *UI* process, so credentials configured/rotated in
the dashboard never reach the running tools until a full restart (the dashboard card
goes green while the tool still uses the boot snapshot); and every `threading.Lock`
guarding a JSON store is useless against the real cross-process contention. Only the
bearer middleware works around this (re-reads `.env` per request). _(D#3 High, D#5, B#4)_

**B. No single source of truth — metadata duplicated across many files, drifts.**
A service is defined in 6–7 places (config fields, `builtin_services`, logos ×2,
discovery fingerprint, `.env.example`, tool map). Tool classification lives in 4+
parallel hand-maintained prefix tables (`TOOL_CATEGORIES`, capability prefixes,
internet prefixes, tool→service map). Adding/renaming anything means editing many
files consistently; drift already exists. _(D#1/2/6/7/8, A#1)_

**C. Orchestration logic stranded in `ui/runtime.py`, with backwards dependencies.**
The agent queue worker, preset folding, disallow-set computation, and notifications
live in the UI-process singleton module. Tool modules import *back* into it
(`tools/rooms.py` → `ui.runtime._agent_mcp_target`), and the MCP-target builder is
copied three times. _(B#1, B#2, B#9)_

**D. Prose-based success classification drives smoke/health flakiness.**
`result_status.text_looks_successful` greps for words like "error"/"traceback" in the
tool's *live* output, so identical upstream states get different verdicts by wording,
and live third-party content containing those words flips a working tool to FAIL.
Plus three divergent probe pipelines and health-cache races. _(C#1/2/5/7, C#3/4/6)_

**E. Manifest bloat — the dominant per-request token cost.**
Default served manifest is all ~209 tools (incl. 58 `pub_` novelty tools); the slicer
only removes whole tools (no description compaction, no dedupe), profiles don't
compose with the global slice, and each profile rebuilds the full registry. _(A#3/4/5/2)_

**F. Non-atomic JSON stores.** `recent_runs`, `custom_integrations`, `ui_prefs` do
naive read-modify-write with no temp file and no lock (corruption + lost-update). _(D#4)_

**G. Real permission gap.** Room seats never get a connection-scope disallow set, so a
room reaches the full tool surface regardless of its declared connections. _(B#3 High)_

**H. Dead code riding every request / image.** The `plutus_tool_slicer.apply` field is
dead but still ~60 tokens on every manifest; legacy `ui/static/spa.js` (58 KB) is still
served; `agent_permissions.build_disallowed*` are unused. _(A#6/9)_

---

## 2. MCP tool system & manifest (agent A)

1. **Three narrowing mechanisms on 4+ taxonomies** — High — `core/profiles.py:79`,
   `core/capabilities.py:14`, `core/tool_annotations.py:22`, `core/tool_registry.py:51`,
   `ui/runtime.py:325-409`. Unify behind one tool-metadata source. `TODO`
2. **Slicer and profiles don't compose** — Med — `ui/runtime.py:188-191`. Intersect
   profile allow-sets with the global exposure set. `TODO`
3. **Default manifest is the full 209 tools** — High — `core/tool_exposure.py:117-125`.
   Ship a lean default slice (novelty/trivia/crypto off) or make profiles the norm. `TODO`
4. **Slicer only removes whole tools** — Med — no description compaction/dedupe; `pub_`
   ×58, github/gitlab mirrors, currency/crypto overlaps. `TODO`
5. **Restart-to-apply inconsistent + per-profile full rebuild** — Med —
   `ui/runtime.py:186-191`. Build a registered superset once, derive views, hot re-slice. `TODO`
6. **`plutus_tool_slicer.apply` is dead + stale field on every manifest** — Med —
   `tools/infrastructure.py:551-567`. Delete field, trim docstring. `TODO` (quick token win)
7. **`resources.py` ignores `allow`** — Low — `tools/resources.py:46`. `TODO`
8. **`prompts.py` allow-semantics mismatch** (names vs playbook ids) — Low —
   `tools/prompts.py:61`. `TODO`
9. **Dead `spa.js` (58 KB) + unused ACL builders** — Low — `ui/static/spa.js`,
   `core/agent_permissions.build_disallowed*`. `TODO`

## 3. Agent / workforce execution (agent B)

1. **Orchestration logic in the UI singleton** — High — `ui/runtime.py:245-478`.
   Extract `core/agent_orchestrator.py`. `DONE` — the execution engine (serial
   queue, worker, run invocation, notify, skipped-run, mcp_target, service_disallow,
   scheduled tool calls) now lives in `core/agent_orchestrator.py`, UI-free and
   injectable; `ui.runtime` keeps thin wrappers for its own callers. Policy
   composition (presets, profile/capability disallow) stays in `ui.runtime` because
   its tests monkeypatch those seams there.
2. **Dependency inversion + triple-duplicated MCP target** — High — `tools/rooms.py`,
   `tools/agents.py`, `ui/runtime.py`. `DONE` — `tools/` no longer imports `ui` at
   all; there is one `agent_orchestrator.mcp_target(cfg)`.
3. **Room connection scope never enforced** — High — `core/workforce.py`. `DONE` —
   `run_room` computes `service_disallow(root, room.mcp_services)` and enforces it on
   every seat (new test `test_every_seat_is_scoped_to_the_rooms_connections`).
   Follow-up: `core/dashboard_api.tool_to_service_map` still lazily imports
   `ui.runtime.all_tool_names` — the tool-registry SSOT (Wave 4) removes that edge.
4. **Cross-process single-slot guarantee is false** — High — `core/agent_runner.py:31`,
   `core/workforce.py:409`. Use a filesystem/OS lock; disk-backed "running" check. `TODO`
5. **Room-to-room file handoff doesn't happen** — Med — `core/workforce.py:467`. Thread
   the predecessor folder through, or fix the docs. `TODO`
6. **`run_agent`/`_execute_*` god-functions + duplicated error ladders** — Med —
   `core/agent_runner.py:1373,968,1254`. Strategy table + one `explain_auth_failure`. `TODO`
7. **Provider fallback tangled into the run loop; circular import** — Med —
   `core/agent_runner.py:1303-1315`, `core/ai_providers.py:1368`. `TODO`
8. **No cost cap on CLI/API runtimes** — Med — `core/agent_runner.py:1039,1499`. `TODO`
9. **Disallow/prefix logic scattered** — Low — consolidate into a `tool_scope` module. `TODO`
10. **Minor data-flow gaps** (room_advise author, MCP-config write failure flag) — Low. `TODO`

## 4. Smoke / health reliability (agent C)

1. **Classifier verdict depends on error phrasing, not state** — High —
   `core/result_status.py:6-23`. Classify on structured signals (is_error/HTTP). `TODO`
2. **Smoke fires live queries then greps live content** — High — `core/tool_registry.py:112-170`.
   Assert on shape, or use fixed inputs. `TODO`
3. **`force=True` silently ignored under lock contention** — High — `ui/runtime.py:497-498`. `DONE`
4. **`_health_cache`/`_health_states` drift; five writers** — Med — `ui/runtime.py:509`.
   One atomically-updated record owned only by `get_health`. `TODO`
5. **Three divergent probe pipelines; regression uses the weakest** — Med/High —
   `core/health_regression.py:107` diffs `batch_health` (no transient handling). `TODO`
6. **Zero-param batch runs serially, 120s/tool** — Med — `core/batch_health.py:22-37`.
   `asyncio.gather` + short per-tool timeout. `TODO`
7. **`looks_like_missing_service_config` over-matches** — Med — `core/tool_registry.py:13-38`. `TODO`
8. **Mutation smoke timing edges** (time-keyed titles, midnight window) — Low/Med. `TODO`
9. **Tests import flakiness** (subprocess skip-on-timeout; DNS assumption) — Med. `TODO`

## 5. Config / registry / state (agent D)

1. **No single source of truth for service metadata (6–7 places)** — High. Introduce a
   `ServiceDef` registry all layers derive from. `TODO`
2. **`config.py` is a 111-field flat blob** — High. Generate cfg from the registry. `TODO`
3. **Live env changes never reach the MCP tool process** — High — `config.py:315-366`.
   Resolve credentials at call time via `env_store`, or reload-signal the MCP process. `TODO`
4. **Naive JSON writers (corruption/lost-update)** — Med — `core/recent_runs.py`,
   `core/custom_integrations.py`, `core/ui_prefs.py`. `DONE` — all routed through
   `core/atomic_json.py` (tmp+fsync+replace, per-path lock, bind-mount fallback).
5. **All locks are `threading.Lock` — useless cross-process** — Med. Advisory file lock. `TODO`
6. **Custom integrations are a parallel config path, never expose tools** — Med. Fold
   onto `ServiceDef` with `source: builtin|custom`. `TODO`
7. **Tool→service gating relies on prefix guessing** — Med — `core/service_registry.py:24-61`.
   Authoritative tool-prefix on the service-def. `TODO`
8. **Two overlapping "open URL" mechanisms** — Low. Collapse into one url-key field. `TODO`

---

## 6. Prioritized remediation roadmap

Sequenced for leverage × safety. Verify `pytest -m "not live"` green after each.

**Wave 1 — safe structural wins + token savings (low risk).**
- `core/atomic_json.py` single atomic writer (tmp+fsync+replace+lock); route the naive
  stores through it. (F / D#4/5) `DONE` — `recent_runs`, `ui_prefs`, and
  `custom_integrations` now write through `atomic_json` (shipped alongside the auth
  work). Cross-process file locking (D#5) deferred with Wave 4.
- Fix health cache: honour `force`. (C#3) `DONE` — a forced refresh no longer serves
  stale under lock contention; it queues for a genuine gather. (Single-record state
  drift, C#4, still `TODO`.)
- Delete dead code: `plutus_tool_slicer.apply` field, `spa.js`, unused ACL builders.
  (H / A#6/9) `TODO`

**Wave 2 — boundaries & dependency direction. `DONE`**
- Extracted `core/agent_orchestrator.py`; killed the `tools → ui.runtime` back-import
  and the triple `mcp_target`; moved the execution engine + service-scope helper to
  core. (C-theme / B#1/2)
- Enforced connection scope in `run_room` (closes the permission gap). (G / B#3)

**Wave 3 — reliability.**
- One structured result classifier; route smoke/batch/dashboard/regression through it;
  deterministic smoke inputs; parallel batch. (D-theme / C#1/2/5/6/7)

**Wave 4 — single sources of truth (larger).**
- `ServiceDef` registry → derive config, health, logos, discovery, tool ownership. (B / D#1/2/6/7/8)
- One tool-metadata registry → slicer, profiles, agent ACLs, capabilities, annotations. (B / A#1)

**Wave 5 — the hard correctness fix.**
- Live-config propagation across the process split (resolve creds at call time). (A / D#3)

**Wave 6 — manifest reduction (the token cost).**
- Lean default slice; profile↔slice composition; description compaction/dedupe. (E / A#2/3/4)

**Parallel track — UX the maintainer asked for.**
- Multi-user auth + real split-screen login page (form + session, admin panel, remember-me,
  default admin/adminadmin + change banner) while keeping Basic auth for curl/n8n. (§7)
- Connection-list marks: real brand logos + emoji fallback for unknowns.

---

## 7. Auth / login redesign (parallel track)

Current: HTTP Basic, single user (`cfg.ui_username`/`ui_password`), `verify_auth`
attached per-router, rate-limited + CSRF-guarded; no login page (raw browser dialog).
Non-browser clients (curl/n8n) authenticate with the same Basic creds — must keep working.

Target: `verify_auth` accepts **either** a valid signed session cookie **or** Basic auth.
A `data/ui_users.json` store (hashed+salted passwords, roles); first-run seeds
`admin`/`adminadmin`. Real `/login` page (split-screen: form left, animated gradient
half-circles right), "remember me" (persistent vs session cookie), an admin panel to
add/remove users and change passwords, and a persistent banner while the default
password is unchanged.
