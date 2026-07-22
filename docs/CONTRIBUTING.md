# Contributing & Development

Single-maintainer homelab project. These are the conventions that keep it consistent.

---

## Dev setup

```bash
python -m venv .venv && . .venv/bin/activate     # optional
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                                  # offline suite, ~1s
python main.py                                    # run locally (needs a .env)
```

Useful env for local dev: `PLUTUS_ALLOW_EMPTY_UI_PASSWORD=1` (no UI password),
`PLUTUS_VERBOSE_ERRORS=1` (see upstream error detail).

---

## Conventions

- **Tools** live in `tools/<domain>.py`, each exposing `register_<domain>_tools(mcp)`.
  Use an `@mcp.tool(name=..., annotations={"readOnlyHint": ..., "destructiveHint": ...})`
  decorator with a pydantic input model (`model_config = ConfigDict(extra="forbid")`).
- **Errors**: return user-facing strings; route exceptions through
  `client._handle_error(e, "<Service>")` so the redaction layer applies. Never
  `return f"Error: {e}"` with a raw exception.
- **Not configured**: guard with `cfg.is_configured(...)` and return a clear message.
- **HTTP**: always pass a timeout to `httpx.AsyncClient` (use `client.TIMEOUT`).
- **Secrets**: never echo passwords/tokens into tool output. Reading files? Go through
  `core/redact.py`.
- **`.env`**: only write via `core/env_store.py` (atomic, validated). Don't hand-roll
  file writes.
- **Paths**: confine filesystem access with `core/path_guard.py`.
- **Tests**: add an offline test in `tests/` for any new pure helper; inject time/IO so
  it stays deterministic and network-free.

### Adding a tool (sketch)

```python
class FooInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., min_length=1, max_length=200)

@mcp.tool(name="foo_search", annotations={"readOnlyHint": True, "destructiveHint": False})
async def foo_search(params: FooInput) -> str:
    if not cfg.is_configured("foo_url", "foo_api_key"):
        return "Error: Foo not configured. Set FOO_URL and FOO_API_KEY in .env"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{cfg.foo_url}/search", headers={"X-Api-Key": cfg.foo_api_key},
                                  params={"q": params.query})
            r.raise_for_status()
            ...
    except Exception as e:
        return _handle_error(e, "Foo")
```

A reversible mutating tool should register a safety level in `core/tool_registry.py` and,
ideally, a create→delete round-trip in `core/smoke_service_tools.py`.

---

## Roadmap — deliberate next steps (not yet done)

These are intentionally deferred to keep changes low-risk; they are the recommended
order for a future refactor pass, not loose ends:

1. **Split `main.py` into `APIRouter`s** by concern (ssh/smb, tool-gate, beta-cache,
   settings, mcp-export). `main.py` would shrink to bootstrap + wiring. Mechanical but
   touches every endpoint, so it should be done as its own reviewed change with the app
   run end-to-end afterward.
2. **Collapse per-tool boilerplate** with a `require_configured(label, *keys)` helper and
   a shared `service_client()` context manager in `client.py`. Apply module-by-module to
   avoid a half-migrated state.
3. **`config.py` → `pydantic-settings` `BaseSettings`** with field validators, and pull
   the scattered timeout literals (`main.py`) into named constants. Lower priority — the
   current class-level-default singleton works and is load-bearing, so change carefully.

The `ui/render.py` single-page HTML builder is intentionally **kept as-is** — it's
decomposed into helpers and escapes user input; introducing a template engine for one
page would be over-engineering.
