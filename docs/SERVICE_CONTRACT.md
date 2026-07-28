# Service Contract — the hard rules every connection card must follow

This is the **contract** that makes the tester, HTTP probe, health state, and
Settings work the same way for every service — and keeps them honest (real
checks, never fake). It is enforced automatically by
`tests/test_service_contract.py` and `tests/test_probe_pipeline.py`, which run in
CI on every push and PR. **If a card breaks a rule, CI goes red before the image
is ever published.**

Add new cards (YouTube, socials, anything) by following these rules. The
validator will tell you immediately if a card is malformed or fake.

---

## 1. Anatomy of a service card

Every entry in `core/builtin_services.py::SERVICES` is a dict:

```python
{
  "id": "jellyfin",                         # unique lowercase slug
  "label": "Jellyfin",                      # human name
  "icon": "🎬", "tag": "media",             # display only
  "section": "selfhosted",                  # selfhosted | system | public
  "desc": "Media server — movies, TV, music",
  "config_keys": [                          # what Settings lets you edit
    ("JELLYFIN_URL", "Server URL", "http://…:8096", False),   # (ENV, label, placeholder, secret)
    ("JELLYFIN_API_KEY", "API Key", "", True),
  ],
  "configured_keys": ("jellyfin_url", "jellyfin_api_key"),    # cfg attrs that must be non-empty
  "health_url": lambda: cfg.jellyfin_url + "/health",         # None if no HTTP probe
  "health_headers": lambda: {"X-Emby-Token": cfg.jellyfin_api_key},
  "tools": [ {"name": "jellyfin_search", "label": "…", "params": [...]}, ... ],
}
```

---

## 2. The rules (enforced by tests)

### R1 — Structure
`id`, `label`, `section`, `tag` are non-empty strings. `id` is a unique
lowercase slug (`^[a-z][a-z0-9_]*$`). `tools` is a list.

### R2 — `config_keys` are well-formed
Each entry is `(ENV_KEY, label, placeholder, secret)`. `ENV_KEY` is a valid,
UI-writable environment variable name (`is_ui_writable_env_key`). `secret` is a
bool. Secret values are **never** returned to the browser — only a `set` flag.

### R3 — `configured_keys` are real
Every name in `configured_keys` is an actual attribute on `Config`
(`config.py`). This is the rule that catches **config/probe drift** — the class
of bug where you configure `JELLYFIN_URL` but the code checks a key that doesn't
exist, so the service is "unconfigured" forever.

### R4 — The probe hits the address you configured
If a card has an HTTP probe (`health_url` is not `None`) **and** a `*_URL`
config key, then the probe URL must be **derived from that configured URL**.
The test sets the URL to a sentinel and asserts `health_url()` contains it. No
hardcoded hosts, no probing a different address than the one you set.

### R5 — Probe callables never crash
`health_url()` and `health_headers()` must not raise when the service is
configured. They are plain lambdas over `cfg.*`; keep them total.

### R6 — Declared tools are real
Every `tools[].name` must be a tool that is actually registered on the MCP
server. A card cannot advertise a tool that does not exist. This is the core
"not fake" rule.

---

## 3. The pipeline (what happens, in order)

1. **Save** — Settings → `POST /env/save` → `env_store.update_env` writes `.env`
   **and** calls `config.apply_live_env`, which pushes every changed key into
   `os.environ` and refreshes the `cfg` singleton **in place**. The running
   process (probes, `is_configured`, and the tools' own `cfg.<svc>_url` reads)
   sees the change immediately — **no restart**. The health cache is dropped so
   the next dashboard fetch re-probes.

2. **Configured?** — `service_utils.is_service_configured` →
   `cfg.is_configured(*configured_keys)` (or `os.getenv` for `config_from_env`
   custom cards). Reads live cfg.

3. **Probe** — `dashboard_health.probe_http_service` GETs `health_url()` with
   `health_headers()`. Interprets the result (see the state table).

4. **State** — `dashboard_health.service_state_from_row` maps the probe row to a
   single UI state.

5. **Test tools** — `smoke_service_tools.run_service_smoke_tools` actually calls
   the tools with validated payloads (read-only auto-run; reversible mutations
   run create→verify→delete; destructive tools are skipped).

---

## 4. Health-state table (deterministic)

| Condition                              | `ok`   | State          | Meaning                                             |
|----------------------------------------|--------|----------------|-----------------------------------------------------|
| Required config missing                | `None` | `unconfigured` | Add URL / credentials in Settings                   |
| No HTTP probe, config satisfied        | `True` | `online`       | Local tool / public API — assumed working           |
| HTTP 200–399                           | `True` | `online`       | Reachable and healthy                               |
| HTTP 401 / 403                         | `None` | `auth_error`   | Reachable but credentials missing/wrong             |
| HTTP 429                               | `None` | `rate_limited` | Reachable but throttling                            |
| HTTP 404                               | `False`| `offline`      | Wrong probe path / not exposing it                  |
| Other 4xx (400, 418, …)                | `False`| `offline`      | Reachable but not serving the probe                 |
| HTTP ≥ 500                             | `False`| `api_error`    | Server error                                        |
| Timeout / connection refused / DNS     | `False`| `offline`      | Not reachable (a fake/bad URL lands here)           |
| No status (non-HTTP, indeterminate)    | `None` | `unknown`      | Couldn't classify                                   |

---

## 5. Adding a card (checklist)

1. Add the `cfg` fields to `config.py` (so `configured_keys` are real — R3).
2. Implement the tool functions and register them (so R6 passes).
3. Add the `SERVICES` entry following the anatomy above.
4. Make `health_url` derive from the configured `*_URL` (R4).
5. Run `python -m pytest tests/test_service_contract.py tests/test_probe_pipeline.py`.
   Green = the card obeys the contract and the pipeline works end to end.
