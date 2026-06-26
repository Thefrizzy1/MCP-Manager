"""Regression diff + baseline persistence (pure logic, no network)."""
from core.health_regression import diff_statuses, load_baseline, save_baseline, _merge_baseline


def test_regression_detected_only_when_was_passing():
    base = {"a": "pass", "b": "fail", "c": "pass"}
    cur = {"a": "fail", "b": "fail", "c": "pass"}
    d = diff_statuses(base, cur)
    assert d["regressions"] == ["a"]
    assert d["recovered"] == []
    assert d["new_broken"] == []


def test_recovery_detected():
    d = diff_statuses({"x": "fail"}, {"x": "pass"})
    assert d["recovered"] == ["x"]
    assert d["regressions"] == []


def test_new_broken_without_baseline_is_not_a_regression():
    # No prior knowledge of "y" -> broken now is informational, not a regression.
    d = diff_statuses({}, {"y": "fail"})
    assert d["regressions"] == []
    assert d["new_broken"] == ["y"]


def test_unset_and_skip_never_count():
    base = {"a": "pass"}
    cur = {"a": "unset", "b": "skip"}
    d = diff_statuses(base, cur)
    assert d == {"regressions": [], "recovered": [], "new_broken": []}


def test_merge_does_not_erase_known_good_with_failure():
    # A regressed tool keeps its 'pass' baseline so it re-alerts until recovery.
    prev = {"a": "pass", "old": "fail"}
    cur = {"a": "fail", "b": "unset"}  # 'b' unset must not be stored
    merged = _merge_baseline(prev, cur)
    assert merged == {"a": "pass", "old": "fail"}


def test_regression_persists_across_runs():
    # was-passing 'a' breaks; baseline stays 'pass' so the next run still flags it.
    base = {"a": "pass"}
    cur = {"a": "fail"}
    assert diff_statuses(base, cur)["regressions"] == ["a"]
    base = _merge_baseline(base, cur)
    assert diff_statuses(base, cur)["regressions"] == ["a"]  # still a regression


def test_recovery_updates_baseline():
    base = {"a": "pass"}
    base = _merge_baseline(base, {"a": "fail"})  # stays pass
    base = _merge_baseline(base, {"a": "pass"})  # recovered
    assert base["a"] == "pass"


def test_new_failing_tool_recorded_as_fail():
    merged = _merge_baseline({}, {"x": "fail"})
    assert merged == {"x": "fail"}


def test_baseline_round_trip(tmp_path):
    save_baseline(tmp_path, {"a": "pass", "b": "fail"})
    assert load_baseline(tmp_path) == {"a": "pass", "b": "fail"}


def test_load_missing_baseline_is_empty(tmp_path):
    assert load_baseline(tmp_path) == {}
