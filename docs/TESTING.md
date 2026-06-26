# Plutus — Testing & Verification

Plutus has **two complementary layers** of testing: a fast offline unit suite for the
plumbing, and an in-app integration system that exercises the live homelab.

---

## 1. Offline unit suite (`tests/`)

Pure-logic tests with **no network and no live services** — they run anywhere (CI,
pre-commit) in ~1s and lock in the security/correctness logic.

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest          # 88 tests
```

Coverage by area:

| File | What it guards |
|---|---|
| `test_path_guard.py` | Boundary-aware path confinement (sibling-prefix & traversal escapes) |
| `test_env_store.py` | Atomic `.env` read/write, key allowlist, newline/empty-password rejection, bool coercion |
| `test_env_key_guard.py` | UI-writable env-key allowlist (`PATH`/`LD_PRELOAD` blocked) |
| `test_redact.py` | Secret masking in file content (env values, auth headers, PEM bodies) |
| `test_error_redaction.py` | `_handle_error` hides upstream bodies/exceptions unless `PLUTUS_VERBOSE_ERRORS` |
| `test_ssrf_guard.py` | `web_fetch` URL screening (scheme + private/loopback/link-local IPs) |
| `test_ssh_exec_validation.py` | `ssh_exec` host/arg validation (command & option injection) |
| `test_bearer_live.py` | Bearer gate reads token/flag live from `.env` |
| `test_rate_limit.py` | Login lockout (window slide, lock expiry, key isolation) |
| `test_mcp_export.py` | Connection-Manager config builder (JSON validity, token gating, http/https) |
| `test_health_regression.py` | Regression diff + baseline (regressions persist until recovery) |
| `test_config_paths.py` | `FILESYSTEM_ALLOWED_PATHS` parsing (CSV + list-literal tolerance) |
| `test_result_status.py` | Success/failure text classifier |
| `test_smoke_helpers.py` | ID/UID parsers used by the round-trip tests |

**Principle:** logic that can be tested without a live service *is* — especially
security boundaries. The two real bugs found in review (path-prefix confinement, error
leakage) each have a regression test here.

---

## 2. In-app verification (live)

Because most tools call live homelab services, integration health is checked *in situ*:

- **`test_all_tools`** (MCP tool) / **`/health/full-report`** (dashboard "Full check"):
  runs every zero-argument tool with safe inputs and classifies each as
  pass / fail / not-configured / skipped via `core/result_status.py` +
  `core/batch_health.py`.
- **Per-service smoke tests** (`core/smoke_service_tools.py`): schema-audits inputs,
  gates by safety level, and runs **reversible round-trips** that create → verify →
  delete → verify-cleanup with emergency cleanup in `finally`. Covered round-trips:
  Nextcloud task, Nextcloud event, and Habitica todo.
- **Service reachability** (`core/dashboard_health.py`): HTTP probes that drive the
  dashboard status dots and `/service/test/{id}`.
- **MCP self-test** (`/api/v1/mcp/selftest`): probes the live `/mcp` endpoint (with the
  bearer token if required) so the Connection Manager can show green/red.

### Safety levels

Tools are classified (`core/tool_registry.py`):

| Level | Meaning | In smoke run |
|---|---|---|
| 0 | Read-only | Run directly |
| 1 | Reversible mutation with cleanup | Run **only** as a create→delete round-trip |
| 2 | Destructive / costly / side-effecting | Skipped |

This is why, e.g., `habitica_add_todo` (level 1) is exercised via a round-trip while
`habitica_delete_task` (level 2) is never run standalone.

---

## 3. Adding tests

- New pure helper in `core/` → add a `tests/test_*.py`. Inject time/IO (see
  `rate_limit`, `ssrf_guard`, `env_store`) so tests stay deterministic and offline.
- New tool with a safe reversible mutation → consider a round-trip in
  `core/smoke_service_tools.py` and register its safety level.
