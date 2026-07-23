# Agent subsystem — audit & verification checklist

A candid audit of the headless-agent / scheduler / playbook subsystem. Findings are
grouped by severity. The **verify-on-container** items are the important ones: parts of
the feature depend on Claude Code CLI behaviour that "tests green" does **not** prove.
Confirmed facts below come from `claude --help` on the build host.

## Confirmed CLI facts (this Claude Code version)
- `--disallowedTools` / `--allowedTools` exist (comma/space-separated tool names).
- `--dangerously-skip-permissions` — *"Bypass all permission checks."*
- `--mcp-config` and **`--strict-mcp-config`** ("only use servers from --mcp-config") exist.
- **`--max-budget-usd <amount>`** exists ("Maximum dollar amount to spend on API calls", `--print` only).
- `--no-session-persistence` exists.
- `claude setup-token` exists ("Set up a long-lived authentication token (requires Claude subscription)").

## 🔴 Verify on the deployed container (feature may be non-functional otherwise)

1. **Does `--disallowedTools` still deny under `--dangerously-skip-permissions`?**
   If skip-permissions bypasses the deny list, the `strict_read`/`safe`/`all` permission
   model does nothing → false security. Test: `claude -p --dangerously-skip-permissions
   --disallowedTools mcp__plutus__docker_stop_container "list containers then stop one"` and
   confirm the stop is refused. If bypassed → switch to `--permission-mode` + an explicit
   **allow-list** (not the `mcp__plutus` wildcard) so the dangerous tools are never granted.
   *Code:* `core/agent_runner.py::build_agent_cmd`.

2. **Interactive in-dashboard OAuth login is unverified and likely broken.**
   `core/agent_login.py::start_interactive/finish_interactive` assume `claude setup-token`
   prints a URL and reads a pasted code on stdin; it more likely uses a browser/localhost
   callback. Verify, or mark the "Log in via browser" button experimental. The **token-paste**
   path (run `claude setup-token`, paste the token) is the reliable one — keep it primary.

3. **Confirm `CLAUDE_CODE_OAUTH_TOKEN` is the right env var** for this version — a saved token
   must actually authenticate a `claude -p` run. If the name differs, web login silently no-ops.

## 🟠 Robustness / cost

4. **Use `--strict-mcp-config`** so the agent can't inherit other MCP servers from the
   container's `~/.claude` — otherwise its reach quietly extends beyond Plutus.
5. **Use `--max-budget-usd`** for real spend enforcement (API-key mode). Today `max_cost_usd`
   only *flags* after the fact. (Subscription usage isn't $-metered; there, timeout + run-cap
   are the guards.)
6. **`--no-session-persistence`** — otherwise each run drops resumable session files (with
   prompt content) into the project dir, accumulating.
7. **Run records grow unbounded and are re-read on every poll.** `total_cost`/`runs_today`/
   `status` call `list_runs(root, 9999)` → glob+read all of `data/agent_runs/` on each dashboard
   status poll (`core/agent_runner.py:255`). Add a retention cap (keep ~200, prune older) and
   cache the running total / today's count.
8. **"safe" mode allows unconfined vault writes.** `obsidian_write_note/append`
   (`tools/obsidian.py:63,83`) have no folder confinement, so a prompt-injected agent can
   overwrite *any* note. Prefer the filesystem output mode (path-guard confined) or a dedicated
   throwaway vault folder; document the residual risk.

## 🟡 Architecture / quality

9. **`main.py` is ~1,410 lines / 62 endpoints** — the god-module the earlier audit flagged, now
   worse after the agent endpoints. Do the `APIRouter` split from CONTRIBUTING.
10. **No endpoint/integration tests.** All offline tests are pure-logic; the `kind:"task"` 422 bug
    shipped and was only caught by an ad-hoc TestClient smoke. Add `tests/test_api_agents.py`.
11. **Two sources of "which tools mutate"** — `agent_permissions.DANGEROUS/WRITE` vs
    `tool_registry.SMOKE_TOOL_EXCLUDE/TOOL_SAFETY_LEVELS`; they can drift. Add a cross-check test.

## 🟢 Ops / minor
12. Agent `claude` subprocesses spawn from the UI child; if the watchdog kills it mid-run the
    subprocess is orphaned — track & kill on shutdown.
13. Skipped scheduled runs (daily cap / queue full) are silent (log only) — notify on skip.
14. `build_text` checks only `_current["running"]`, not the queue — a "build with Claude" can
    briefly race a just-dequeued run.

## Recommended order
1. Verify #1–#3 on the container (decides whether the security/login features work at all).
2. Safe offline code wins: `--strict-mcp-config`, `--no-session-persistence`, `--max-budget-usd`
   (#4–#6); prune/cache run records (#7); endpoint tests (#10); notify-on-skip (#13).
3. Bigger: router split (#9); confine the vault-write story (#8).
