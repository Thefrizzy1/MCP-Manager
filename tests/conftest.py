"""Shared fixtures.

Nothing here is optional bookkeeping — the autouse fixture below fixes a real
cross-test leak that made the suite order- and timing-dependent.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def agent_preconditions(monkeypatch):
    """Satisfy the two *environment* preconditions run_agent checks before spawning.

    run_agent refuses to start when there is no usable credential, and when the
    Claude CLI cannot be resolved on disk. Both are properties of the machine, not
    of the code under test — so any test about something else (cost caps, pipe
    plumbing, what a run record stores) has to stub them, or it passes on a
    developer box that happens to have Claude Code installed and logged in, and
    fails on a runner that has neither. That exact divergence has broken CI twice.

    Tests that genuinely cover credential selection or CLI resolution do not use
    this fixture; they drive those seams themselves.
    """
    from core import agent_runner as AR
    from core import ai_providers as AP

    monkeypatch.setattr(AR, "legacy_credential_source", lambda: ("cli", "test credential"))
    monkeypatch.setattr(AP, "resolve_cli", lambda name: f"/usr/bin/{name}")


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
