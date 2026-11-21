"""Model lab runner: the parts that guard files and secrets, without a server."""
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("lab", Path(__file__).parents[1] / "lab" / "lab.py")
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)


def test_only_files_downloaded_this_run_are_deleted(tmp_path, monkeypatch):
    models = tmp_path / "models"
    (models / "vae").mkdir(parents=True)
    (models / "vae" / "old.safetensors").write_bytes(b"old")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "prod.safetensors").write_bytes(b"prod")
    run = tmp_path / "run"
    run.mkdir()
    (run / "models_before.json").write_text(json.dumps(lab.snapshot(models)))
    (models / "vae" / "new.safetensors").write_bytes(b"new")
    (models / "linked").symlink_to(shared)                      # a shared folder linked in
    monkeypatch.setattr(lab, "permission", lambda cfg, action: "allow")
    out = lab.cleanup({"models_dir": str(models)}, run)
    assert out["deleted"] == 1
    assert (models / "vae" / "old.safetensors").exists()
    assert (shared / "prod.safetensors").exists()
    assert not (models / "vae" / "new.safetensors").exists()


def test_nothing_is_deleted_without_permission_or_snapshot(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    (models / "a.gguf").write_bytes(b"x")
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(lab, "permission", lambda cfg, action: "allow")
    assert lab.cleanup({"models_dir": str(models)}, run)["deleted"] == 0      # no snapshot
    (run / "models_before.json").write_text("[]")
    monkeypatch.setattr(lab, "permission", lambda cfg, action: "ask")
    assert lab.cleanup({"models_dir": str(models)}, run)["deleted"] == 0
    assert (models / "a.gguf").exists()


def test_sessions_never_get_the_tokens(monkeypatch):
    monkeypatch.setenv("LAB_GH_TOKEN", "secret")
    monkeypatch.setenv("PLUTUS_MCP_TOKEN", "secret")
    monkeypatch.setenv("HOME", "/home/lab")
    env = lab.model_env()
    assert "LAB_GH_TOKEN" not in env and "PLUTUS_MCP_TOKEN" not in env and env["HOME"] == "/home/lab"


def test_prompts_render_with_every_placeholder_filled():
    keys = ("DATE RUN_DIR REPO MODELS_DIR INPUTS_DIR COMFY_URL COMFY_DIR STYLE LESSONS "
            "MANIFEST ASSIGNMENT MAX_GB ISSUES SLUG").split()
    values = {k: k.lower() for k in keys}
    for name in ("build.md", "critique.md"):
        assert "{{" not in lab.render(name, **values)
    with pytest.raises(ValueError):
        lab.render("build.md", DATE="x")


def test_unreachable_plutus_never_widens(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(lab, "plutus", boom)
    cfg = {"state_dir": str(tmp_path)}
    assert lab.permission(cfg, "lab.merge_pr") == "ask"
    # A cached allow survives an outage only for deleting its own downloads.
    (tmp_path / "permissions_cache.json").write_text(
        '{"lab.merge_pr": "allow", "lab.delete_own_models": "allow", "lab.open_pr": "never"}')
    assert lab.permission(cfg, "lab.merge_pr") == "ask"
    assert lab.permission(cfg, "lab.delete_own_models") == "allow"
    assert lab.permission(cfg, "lab.open_pr") == "never"


def test_the_workflow_maker_list_comes_before_the_local_queue(tmp_path, monkeypatch):
    (tmp_path / "queue.json").write_text('[{"task": "build", "model": "from file"}]')
    monkeypatch.setattr(lab, "plutus", lambda cfg, tool, *a, **k: '{"slug": "ltx-2.5", "name": "LTX 2.5"}')
    assert lab.next_assignment({}, tmp_path)["slug"] == "ltx-2.5"
    monkeypatch.setattr(lab, "plutus", lambda cfg, tool, *a, **k: "{}")
    assert lab.next_assignment({}, tmp_path)["model"] == "from file"
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(lab, "plutus", boom)
    assert lab.next_assignment({}, tmp_path)["model"] == "from file"
    lab.tell({}, "still fine when Plutus is down")          # must not raise
