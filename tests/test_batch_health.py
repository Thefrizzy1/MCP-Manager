"""The zero-param health batch: order preserved, and probes run concurrently
under a bound (the serial version stalled the whole batch on one slow tool)."""
from __future__ import annotations

import asyncio

import pytest

from core import batch_health as B


class _FakeManager:
    def get_tool(self, name):
        class _T:
            fn = name
        return _T()


@pytest.fixture
def ready_env(monkeypatch):
    # Every tool is "configured" and its payload is trivial, so the batch actually
    # invokes each probe rather than short-circuiting to unset/skip.
    monkeypatch.setattr(B, "is_tool_environment_ready", lambda name: True)
    monkeypatch.setattr(B, "merged_tool_payload", lambda name, user: {})


def test_batch_preserves_tool_order(monkeypatch, ready_env):
    async def fake_invoke(fn, payload=None):
        return "OK"

    monkeypatch.setattr(B, "invoke_mcp_tool_fn", fake_invoke)
    rows = asyncio.run(B.run_health_batch_for_ui(_FakeManager()))
    assert [r["name"] for r in rows] == list(B.ZERO_PARAM_HEALTH_TOOLS)
    assert all(r["kind"] == "pass" for r in rows)


def test_batch_runs_probes_concurrently_within_the_bound(monkeypatch, ready_env):
    """Deterministic (no wall-clock): count peak simultaneous probes. Serial would
    peak at 1; the semaphore caps it at the requested concurrency."""
    state = {"active": 0, "peak": 0}

    async def counting_invoke(fn, payload=None):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return "OK"

    monkeypatch.setattr(B, "invoke_mcp_tool_fn", counting_invoke)
    asyncio.run(B.run_health_batch_for_ui(_FakeManager(), concurrency=5))
    assert state["peak"] > 1, "probes did not run concurrently"
    assert state["peak"] <= 5, f"concurrency bound exceeded: peak={state['peak']}"


def test_a_hung_tool_times_out_without_a_kind_of_pass(monkeypatch, ready_env):
    async def hang(fn, payload=None):
        await asyncio.sleep(10)
        return "OK"

    monkeypatch.setattr(B, "invoke_mcp_tool_fn", hang)
    rows = asyncio.run(B.run_health_batch_for_ui(_FakeManager(), timeout=0.05))
    assert rows and all(r["kind"] == "fail" and "timeout" in r["detail"] for r in rows)
