"""Agent playbooks — seed, CRUD, prompt rendering (offline)."""
import pytest

from core import agent_tasks as at


def test_seed_installs_starters(tmp_path):
    tasks = at.seed_if_empty(tmp_path)
    ids = {t["id"] for t in tasks}
    assert "competitor-research" in ids
    assert "ai-comfyui-trends" in ids
    assert len(tasks) >= 5
    # idempotent — second call doesn't duplicate
    assert len(at.seed_if_empty(tmp_path)) == len(tasks)


def test_get_task(tmp_path):
    at.seed_if_empty(tmp_path)
    t = at.get_task(tmp_path, "weekly-digest")
    assert t and "digest" in t["name"].lower()


def test_upsert_new_and_update(tmp_path):
    created = at.upsert_task(tmp_path, {"name": "My task", "prompt": "do research"})
    assert created["id"]
    updated = at.upsert_task(tmp_path, {"id": created["id"], "name": "My task v2", "prompt": "do more"})
    assert updated["id"] == created["id"]
    assert updated["name"] == "My task v2"
    assert len([t for t in at.load_tasks(tmp_path) if t["id"] == created["id"]]) == 1


def test_upsert_requires_name_and_prompt(tmp_path):
    with pytest.raises(ValueError):
        at.upsert_task(tmp_path, {"name": "", "prompt": "x"})
    with pytest.raises(ValueError):
        at.upsert_task(tmp_path, {"name": "x", "prompt": ""})


def test_delete(tmp_path):
    t = at.upsert_task(tmp_path, {"name": "tmp", "prompt": "x"})
    assert at.delete_task(tmp_path, t["id"]) is True
    assert at.get_task(tmp_path, t["id"]) is None
    assert at.delete_task(tmp_path, "nope") is False


def test_render_prompt():
    out = at.render_prompt("Read {{LIBRARY}}/x on {{DATE}}", library="research", date="2026-07-22")
    assert out == "Read research/x on 2026-07-22"
