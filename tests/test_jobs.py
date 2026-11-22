"""Job board: stages are read off the folder, manual facts win, paths stay confined."""
import os

import pytest

from core import jobs


@pytest.fixture
def yt(tmp_path, monkeypatch):
    root = tmp_path / "yt"
    job = root / "20261111_Local Relight"
    (job / "upload").mkdir(parents=True)
    (job / "edit").mkdir()
    (job / "SCRIPT.md").write_text("script")
    (job / "upload" / "thumb A.png").write_bytes(b"x")
    (root / "00_Library").mkdir()
    (root / "00_Library" / "secret.md").write_text("no")
    (root / "_to delete").mkdir()
    monkeypatch.setenv("YOUTUBE_ROOT", str(root))
    jobs._cache["ts"] = 0.0
    return tmp_path


def _status(job):
    return {s["id"]: s["status"] for s in job["stages"]}


def test_only_dated_folders_are_jobs(yt):
    assert [j["id"] for j in jobs.list_jobs(yt, force=True)["jobs"]] == ["20261111_Local Relight"]


def test_packaging_first_does_not_mark_the_script_done(yt):
    st = _status(jobs.get_job(yt, "20261111_Local Relight"))
    assert st["script"] == "active" and st["packaging"] == "active" and st["publish"] == "todo"


def test_final_export_finishes_the_edit_and_everything_before_it(yt):
    (yt / "yt" / "20261111_Local Relight" / "edit" / "Local Relight (1080p).mp4").write_bytes(b"x")
    job = jobs.get_job(yt, "20261111_Local Relight")
    st = _status(job)
    assert st["script"] == "done" and st["edit"] == "done" and job["current"] == "packaging"


def test_manual_facts_win_and_auto_restores(yt):
    jid = "20261111_Local Relight"
    job = jobs.update_job(yt, jid, {"stages": {"script": "done"}, "deadline": "2026-12-01"})
    assert _status(job)["script"] == "done" and job["deadline"] == "2026-12-01"
    assert _status(jobs.update_job(yt, jid, {"stages": {"script": "auto"}}))["script"] == "active"
    assert _status(jobs.update_job(yt, jid, {"publish_url": "https://youtu.be/x"}))["publish"] == "done"
    with pytest.raises(ValueError):
        jobs.update_job(yt, jid, {"deadline": "next week"})


def test_files_cannot_escape_the_job_folder(yt):
    jid = "20261111_Local Relight"
    assert jobs.resolve_file(jid, "SCRIPT.md")
    assert jobs.resolve_file(jid, "../00_Library/secret.md") is None
    assert jobs.job_folder("../yt") is None and jobs.job_folder("00_Library") is None
    assert jobs.list_folder(jid, "..") is None


def test_old_untouched_jobs_archive_themselves_unless_pinned(yt):
    jid = "20261111_Local Relight"
    old = 1_600_000_000
    for dirpath, _dirs, files in os.walk(yt / "yt" / jid):
        for f in files:
            os.utime(os.path.join(dirpath, f), (old, old))
    assert jobs.get_job(yt, jid)["archived"] is True
    assert jobs.update_job(yt, jid, {"pinned": True})["archived"] is False


def test_uploads_are_matched_to_jobs_and_shorts_stay_open(yt):
    jid = "20261111_Local Relight"
    jobs.save_uploads(yt, "@me", [
        {"id": "aaaaaaaaaaa", "title": "I Replaced Higgsfield Relight With My RTX 3060 (Free, Local)",
         "published": "2026-11-13", "kind": "video"},
        {"id": "bbbbbbbbbbb", "title": "Local Relight", "published": "2026-10-01", "kind": "video"},
        {"id": "ccccccccccc", "title": "Local Relight short", "published": "2026-11-14", "kind": "short"},
    ])
    job = jobs.get_job(yt, jid)
    st = _status(job)
    assert job["published"]["id"] == "aaaaaaaaaaa"          # not the older one, not the short
    assert job["youtube_thumbnail"].endswith("/aaaaaaaaaaa/mqdefault.jpg")
    assert st["publish"] == "done" and st["edit"] == "done"
    assert st["shorts"] == "todo" and job["current"] == "shorts" and job["archived"] is False
    assert jobs.update_job(yt, jid, {"stages": {"shorts": "done"}})["archived"] is True
    assert jobs.update_job(yt, jid, {"publish_match": False, "stages": {"shorts": "auto"}})["published"] is None


def test_the_upload_pack_settles_a_title_with_no_shared_words():
    cands = [{"id": "a", "start": "2026-10-30", "title": "LTX VFX LoRAs",
              "pack": "## Titles\\n1. No Seedance 2.5 Needed: Free AI VFX in 2 to 4 Minutes\\n"},
             {"id": "b", "start": "2026-11-01", "title": "Something else", "pack": ""}]
    vids = [{"id": "v", "title": "No Seedance 2.5 Needed: Free AI VFX in 2 to 4 Minutes",
             "published": "2026-11-07", "kind": "video"}]
    assert jobs.match_uploads(cands, vids) == {"a": vids[0]}


def test_labels_tag_videos_and_sponsored_folders_label_themselves(yt):
    (yt / "yt" / "20261118_Meshy (sponsored)").mkdir()
    board = jobs.list_jobs(yt, force=True)
    by = {j["id"]: j for j in board["jobs"]}
    assert [x["id"] for x in board["labels"]] == ["model", "general", "sponsored"]
    assert by["20261118_Meshy (sponsored)"]["label"] == "sponsored" and by["20261111_Local Relight"]["label"] == ""
    assert jobs.update_job(yt, "20261111_Local Relight", {"label": "model"})["label"] == "model"
    with pytest.raises(ValueError):
        jobs.update_job(yt, "20261111_Local Relight", {"label": "nope"})
    jobs.save_labels(yt, [{"name": "Model review", "id": "model", "color": "#8b5cf6"}, {"name": "Tutorial", "color": "#3b82f6"}])
    assert [x["id"] for x in jobs.labels(yt)] == ["model", "tutorial"]
    with pytest.raises(ValueError):
        jobs.save_labels(yt, [{"name": "Bad", "color": "purple"}])
