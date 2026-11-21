"""Model lab board: queueing, resuming, progress reports."""
import pytest

from core import lab_board as B


def test_queue_then_report_through_to_built(tmp_path):
    assert B.next_queued(tmp_path) is None
    B.queue(tmp_path, "LTX-2.5", "LTX 2.5")
    B.queue(tmp_path, "flux-3", "Flux 3")
    assert B.next_queued(tmp_path)["slug"] == "ltx-2.5"
    B.report(tmp_path, "flux-3", "downloading", "testing")
    assert B.next_queued(tmp_path)["slug"] == "flux-3"      # an interrupted run resumes
    row = B.report(tmp_path, "flux-3", "PR open", "built", pr="https://x/pull/1")
    assert row["status"] == "built" and row["pr"] and len(row["log"]) == 2
    assert B.next_queued(tmp_path)["slug"] == "ltx-2.5"
    assert B.heartbeat(tmp_path)["slug"] == "flux-3"


def test_bad_status_is_refused_and_skip_removes_from_the_queue(tmp_path):
    B.queue(tmp_path, "a")
    with pytest.raises(ValueError):
        B.report(tmp_path, "a", status="done-ish")
    B.set_status(tmp_path, "a", "skipped")
    assert B.next_queued(tmp_path) is None
    B.set_status(tmp_path, "a", "")
    assert B.entries(tmp_path) == {}
