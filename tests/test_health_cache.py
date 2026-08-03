"""The health cache has one owner: cache, state map and timestamp move together,
so an invalidation or refresh can never leave _health_states stale (the drift
that let system.py serve a state map out of step with the cache)."""
from __future__ import annotations

import pytest

from ui import runtime as R


@pytest.fixture(autouse=True)
def _restore_health_globals():
    snap = (dict(R._health_cache), dict(R._health_states), R._health_ts)
    yield
    R._health_cache, R._health_states, R._health_ts = dict(snap[0]), dict(snap[1]), snap[2]


def test_invalidate_health_resets_all_three_together():
    R._health_cache = {"x": True}
    R._health_states = {"x": "online"}
    R._health_ts = 123.0
    R.invalidate_health()
    assert R._health_cache == {}
    assert R._health_states == {}   # the field earlier ad-hoc resets forgot
    assert R._health_ts == 0.0


def test_set_health_builds_the_state_map_from_rows():
    R.invalidate_health()
    R._set_health({"a": True}, [{"id": "a", "state": "online"}, {"id": "b", "state": "offline"}])
    assert R._health_cache == {"a": True}
    assert R._health_states == {"a": "online", "b": "offline"}
    assert R._health_ts > 0


def test_set_service_health_updates_both_maps_in_step():
    R.invalidate_health()
    R.set_service_health("svc", True, "online")
    assert R._health_cache["svc"] is True
    assert R._health_states["svc"] == "online"
