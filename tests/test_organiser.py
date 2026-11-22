"""Organiser: only live videos move, only within the root, only when allowed."""
import datetime as dt

import pytest

from core import jobs, organiser, permissions


@pytest.fixture
def sorted_root(tmp_path, monkeypatch):
    root = tmp_path / "yt"
    for name in ("20261103_Live long ago", "20261119_Live yesterday", "20261118_Not live"):
        (root / "01_Open_Jobs" / name / "upload").mkdir(parents=True)
        (root / "01_Open_Jobs" / name / "SCRIPT.md").write_text("s")
    (root / "02_Archive").mkdir()
    monkeypatch.setenv("YOUTUBE_ROOT", str(root))
    jobs._cache.update(ts=0.0, root="")
    today = dt.date.today()
    jobs.save_uploads(tmp_path, "@me", [
        {"id": "aaaaaaaaaaa", "title": "Live long ago", "kind": "video",
         "published": (today - dt.timedelta(days=20)).isoformat()},
        {"id": "bbbbbbbbbbb", "title": "Live yesterday", "kind": "video",
         "published": (today - dt.timedelta(days=1)).isoformat()},
    ])
    # folder dates must not be after the publish dates for the match
    return tmp_path


def _fix_dates(tmp_path):
    """Rename the fixtures so each folder starts before its video went live."""
    root = tmp_path / "yt" / "01_Open_Jobs"
    old = (dt.date.today() - dt.timedelta(days=25)).strftime("%Y%m%d")
    new = (dt.date.today() - dt.timedelta(days=3)).strftime("%Y%m%d")
    (root / "20261103_Live long ago").rename(root / f"{old}_Live long ago")
    (root / "20261119_Live yesterday").rename(root / f"{new}_Live yesterday")
    (root / "20261118_Not live").rename(root / f"{new}_Not live")
    return f"{old}_Live long ago", f"{new}_Live yesterday", f"{new}_Not live"


def test_ask_waits_then_moves_only_what_is_due(sorted_root):
    long_ago, yesterday, not_live = _fix_dates(sorted_root)
    rows = organiser.run(sorted_root)                            # default permission: ask
    assert [(r["job"], r["result"]) for r in rows] == [(long_ago, "waiting for your OK")]
    assert (sorted_root / "yt" / "01_Open_Jobs" / long_ago).is_dir()
    req = permissions.requests(sorted_root, "pending")[0]
    permissions.decide(sorted_root, req["id"], True)
    rows = organiser.run(sorted_root)
    assert rows[0]["result"] == "moved"
    assert (sorted_root / "yt" / "02_Archive" / long_ago[:4] / long_ago[4:6] / long_ago / "SCRIPT.md").is_file()
    assert (sorted_root / "yt" / "01_Open_Jobs" / yesterday).is_dir()        # still in its grace period
    assert (sorted_root / "yt" / "01_Open_Jobs" / not_live).is_dir()
    assert permissions.requests(sorted_root, "approved") == []
    assert organiser.run(sorted_root) == []                      # nothing left that is due


def test_shorts_done_ends_the_grace_period_and_dry_run_moves_nothing(sorted_root):
    long_ago, yesterday, _ = _fix_dates(sorted_root)
    permissions.set_value(sorted_root, organiser.ACTION, "allow")
    jobs.update_job(sorted_root, yesterday, {"stages": {"shorts": "done"}})
    rows = organiser.run(sorted_root, dry_run=True)
    assert {r["job"] for r in rows} == {long_ago, yesterday} and all(r["result"] == "would move" for r in rows)
    assert (sorted_root / "yt" / "01_Open_Jobs" / yesterday).is_dir()
    assert {r["result"] for r in organiser.run(sorted_root)} == {"moved"}


def test_never_overwrites_and_never_touches_other_folders(sorted_root):
    long_ago, _, _ = _fix_dates(sorted_root)
    permissions.set_value(sorted_root, organiser.ACTION, "allow")
    organiser.destination(long_ago).mkdir(parents=True)          # something is already there
    assert organiser.run(sorted_root)[0]["result"].startswith("failed")
    assert (sorted_root / "yt" / "01_Open_Jobs" / long_ago).is_dir()
    with pytest.raises(ValueError):
        organiser.archive("20261014_Not a folder here")
    permissions.set_value(sorted_root, organiser.ACTION, "never")
    assert organiser.run(sorted_root)[0]["result"] == "not allowed"
