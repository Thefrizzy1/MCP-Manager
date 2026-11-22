"""Studio: tasks wait for each other, skips do not block, nothing runs twice."""
import pytest

from core import jobs, studio as S


def test_script_waits_for_research_and_a_skip_lets_it_through(tmp_path):
    r = S.add_task(tmp_path, "v1", "research")
    w = S.add_task(tmp_path, "v1", "script", after=r["id"])
    assert S.add_task(tmp_path, "v1", "research")["id"] == r["id"]          # not queued twice
    first = S.next_task(tmp_path, ["researcher", "writer"], "gpu")
    assert first["kind"] == "research" and first["status"] == "running"
    assert S.next_task(tmp_path, ["researcher", "writer"], "gpu") == {}      # script waits
    S.finish_task(tmp_path, r["id"], "done", "research saved")
    assert S.next_task(tmp_path, ["writer"], "gpu")["id"] == w["id"]
    # a video that needs no research: recorded, crossed out, and the script goes ahead
    skip = S.add_task(tmp_path, "v2", "research", status="not_required", reason="a fun experiment")
    w2 = S.add_task(tmp_path, "v2", "script", after=skip["id"])
    assert S.next_task(tmp_path, ["writer"], "gpu")["id"] == w2["id"]
    assert S.stage_marks(tmp_path) == {"v2": {"research": "skipped"}}


def test_a_failed_dependency_blocks_and_a_handed_back_task_runs_again(tmp_path):
    r = S.add_task(tmp_path, "v", "research")
    w = S.add_task(tmp_path, "v", "script", after=r["id"])
    t = S.next_task(tmp_path, ["researcher"], "gpu")
    S.finish_task(tmp_path, t["id"], "queued", reason="usage limit")         # handed back
    assert S.next_task(tmp_path, ["researcher"], "gpu")["id"] == r["id"]
    S.finish_task(tmp_path, r["id"], "failed", reason="no sources")
    assert S.next_task(tmp_path, ["writer"], "gpu") == {}
    assert {x["id"]: x["blocked"] for x in S.tasks(tmp_path, "v")}[w["id"]] is True
    with pytest.raises(ValueError):
        S.add_task(tmp_path, "v", "fly_to_the_moon")


def test_runs_make_the_agents_timeline_and_show_where_it_works(tmp_path):
    S.add_task(tmp_path, "20261119_Video", "script")
    t = S.next_task(tmp_path, ["writer"], "gpu-server")
    st = S.agent_states(tmp_path)["writer"]
    assert st["now"]["machine"] == "gpu-server" and st["now"]["job"] == "20261119_Video"
    S.finish_task(tmp_path, t["id"], "done", "SCRIPT.md written")
    st = S.agent_states(tmp_path)["writer"]
    assert "now" not in st and st["last"]["ok"] == 1 and S.runs(tmp_path, "writer")[0]["text"] == "SCRIPT.md written"


def test_chat_messages_are_all_kept_and_answered_in_order(tmp_path):
    S.post(tmp_path, "v", "user", "make the hook shorter")
    a = S.add_task(tmp_path, "v", "chat", "make the hook shorter")
    b = S.add_task(tmp_path, "v", "chat", "and find three shorts")
    assert a["id"] != b["id"] and S.next_task(tmp_path, ["manager"])["id"] == a["id"]
    assert [m["text"] for m in S.thread(tmp_path, "v")] == ["make the hook shorter"]


def test_only_actions_that_make_sense_are_offered():
    def job(script="", thumbs=(), st=None):
        stages = [{"id": s, "status": (st or {}).get(s, "todo")} for s in jobs.STAGES]
        return {"stages": stages, "script": script, "thumbnails": list(thumbs)}
    ids = lambda j, tasks=(): [a["id"] for a in S.actions(j, list(tasks))]
    assert ids(job()) == ["research", "script"]
    assert ids(job(), [{"kind": "script", "status": "running"}]) == ["research"]          # already in hand
    assert ids(job(script="SCRIPT.md", st={"research": "done", "script": "active"})) == ["film", "thumbnail"]
    assert "thumbnail" not in ids(job(script="SCRIPT.md", thumbs=["upload/t.png"]))        # it has one
    all_done = {s: "done" for s in jobs.STAGES if s != "shorts"}
    assert ids(job(script="SCRIPT.md", thumbs=["t"], st=all_done)) == ["shorts"]
    assert ids(job(script="SCRIPT.md", thumbs=["t"], st=dict(all_done, shorts="active"))) == []


def test_documents_are_versioned_and_confined(tmp_path, monkeypatch):
    root = tmp_path / "yt"
    (root / "01_Open_Jobs").mkdir(parents=True)
    (root / "00_Library" / "Agents").mkdir(parents=True)
    (root / "00_Library" / "Agents" / "VOICE.md").write_text("short sentences")
    monkeypatch.setenv("YOUTUBE_ROOT", str(root))
    jobs._cache.update(ts=0.0, root="")
    job = jobs.create_job("A fun test: idea?", "try the thing")
    assert (root / "01_Open_Jobs" / job / "IDEA.md").is_file() and "?" not in job
    assert jobs.write_doc(job, "SCRIPT.md", "v1")["previous_kept_as"] == ""
    kept = jobs.write_doc(job, "SCRIPT.md", "v2")["previous_kept_as"]
    assert kept.startswith("_old/SCRIPT_old_") and jobs.read_doc(job, "SCRIPT.md") == "v2"
    assert jobs.read_doc(job, kept) if False else (root / "01_Open_Jobs" / job / kept).read_text() == "v1"
    jobs.write_doc(job, "research/RESEARCH.md", "facts")
    for bad in ("../x.md", "edit/deep/x.md", "SCRIPT.exe", "secret/x.md"):
        with pytest.raises(ValueError):
            jobs.write_doc(job, bad, "x")
    assert jobs.library_read("Agents/VOICE.md") == "short sentences"
    with pytest.raises(ValueError):
        jobs.library_read("../01_Open_Jobs/" + job + "/SCRIPT.md")
    with pytest.raises(FileExistsError):
        jobs.create_job("A fun test: idea?")


def test_scheduled_rounds_get_a_place_on_the_timeline(tmp_path):
    S.mark_round(tmp_path, "researcher", True, "Shift started", "gpu-server")
    S.mark_round(tmp_path, "researcher", True, "Reading transcripts", "gpu-server")      # same round
    assert S.agent_states(tmp_path)["researcher"]["now"]["machine"] == "gpu-server"
    S.mark_round(tmp_path, "researcher", False, "Brief written: 3 ideas")
    rs = S.runs(tmp_path, "researcher")
    assert len(rs) == 1 and rs[0]["ok"] == 1 and rs[0]["kind"] == "round" and rs[0]["finished"] > 0
    S.mark_round(tmp_path, "researcher", False, "stray done report")                     # nothing open: ignored
    assert len(S.runs(tmp_path, "researcher")) == 1


def test_a_skipped_step_shows_skipped_even_when_later_files_exist(tmp_path, monkeypatch):
    root = tmp_path / "yt"
    (root / "01_Open_Jobs" / "20261122_Fun test").mkdir(parents=True)
    (root / "01_Open_Jobs" / "20261122_Fun test" / "SCRIPT.md").write_text("s")
    monkeypatch.setenv("YOUTUBE_ROOT", str(root))
    jobs._cache.update(ts=0.0, root="")
    S.add_task(tmp_path, "20261122_Fun test", "research", status="not_required", reason="fun")
    st = {s["id"]: s["status"] for s in jobs.get_job(tmp_path, "20261122_Fun test")["stages"]}
    assert st["research"] == "skipped" and st["script"] == "active"
