"""Bearer gate reads token/flag live from .env (fixes multiprocess staleness)."""
import core.mcp_bearer_middleware as mw


def _reset_cache():
    mw._cache = None
    mw._cache_ts = 0.0


def test_disabled_when_flag_off(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(mw, "read_env", lambda: {"MCP_REQUIRE_BEARER": "false"})
    require, token = mw._auth_config()
    assert require is False


def test_enabled_with_token(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(mw, "read_env", lambda: {"MCP_REQUIRE_BEARER": "true", "MCP_BEARER_TOKEN": "abc"})
    require, token = mw._auth_config()
    assert require is True
    assert token == "abc"


def test_change_visible_after_cache_expiry(monkeypatch):
    _reset_cache()
    state = {"MCP_REQUIRE_BEARER": "false"}
    monkeypatch.setattr(mw, "read_env", lambda: dict(state))
    assert mw._auth_config()[0] is False
    # Flip the .env and expire the TTL cache -> the gate must see the new value.
    state["MCP_REQUIRE_BEARER"] = "true"
    state["MCP_BEARER_TOKEN"] = "xyz"
    mw._cache_ts = 0.0  # force refresh
    require, token = mw._auth_config()
    assert require is True and token == "xyz"
