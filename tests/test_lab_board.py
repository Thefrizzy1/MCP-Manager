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


def test_review_details_are_kept_for_the_publish_check(tmp_path):
    row = B.report(tmp_path, "sdxl", "ready", "built", pr="https://x/pull/2",
                   review={"repo": "o/r", "branch": "lab/x", "folder": "workflows/sdxl/a",
                           "samples": ["workflow.png", "sample-1.webp"], "tested": "2 runs", "junk": "dropped"})
    assert row["review"]["samples"] == ["workflow.png", "sample-1.webp"] and "junk" not in row["review"]


def test_the_3060_result_arrives_later_and_offers_an_update(tmp_path):
    B.report(tmp_path, "sdxl", "live", "published", pr="https://x/pull/2")
    row = B.report(tmp_path, "sdxl", "41 s per image, 10.8 GB peak", gpu3060="done", update_pr="https://x/pull/9")
    assert row["status"] == "published" and row["gpu3060"]["status"] == "done"
    assert row["update_pr"].endswith("/9") and row["publish_requested"] is False
    with pytest.raises(ValueError):
        B.report(tmp_path, "sdxl", gpu3060="maybe")


def test_to_front_makes_a_model_next(tmp_path):
    for s in ("a", "b", "flux-3"):
        B.queue(tmp_path, s)
    assert B.next_queued(tmp_path)["slug"] == "a"
    B.to_front(tmp_path, "flux-3")
    assert B.next_queued(tmp_path)["slug"] == "flux-3"
    B.to_front(tmp_path, "new-one", "New One")          # not on the list yet: queued and first
    assert B.next_queued(tmp_path)["slug"] == "new-one"


def test_reorder_sets_the_lab_queue_order(tmp_path):
    for s in ("a", "b", "c", "d"):
        B.queue(tmp_path, s)
    B.report(tmp_path, "a", status="published")
    assert B.reorder(tmp_path, ["d", "b", "nope", "a"]) == ["d", "b", "c"]   # unknown / not queued ignored
    assert B.next_queued(tmp_path)["slug"] == "d"
    B.reorder(tmp_path, ["c"])
    assert B.next_queued(tmp_path)["slug"] == "c"
