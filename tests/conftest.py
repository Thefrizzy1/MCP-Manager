"""Shared fixtures.

Nothing here is optional bookkeeping — the autouse fixture below fixes a real
cross-test leak that made the suite order- and timing-dependent.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_bearer_gate_cache():
    """Clear the bearer gate's module-level auth cache around every test.

    ``core.mcp_bearer_middleware`` caches the parsed auth config for 3 seconds at
    module scope so the live .env toggle stays cheap. Tests that stub ``read_env``
    (see test_bearer_live.py) leave that cache populated with
    ``require=True, token=…``; any test that makes a real request through the gate
    inside the TTL then gets an unexplained 401.

    That is exactly what broke CI while passing locally: the suite ran slowly
    enough here for the TTL to lapse before the routing tests, and fast enough on
    the runner that it did not.
    """
    import core.mcp_bearer_middleware as mw

    mw._cache = None
    mw._cache_ts = 0.0
    yield
    mw._cache = None
    mw._cache_ts = 0.0
