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


def test_a_usage_limit_is_not_a_failed_model(tmp_path):
    log_file = tmp_path / "build1.log"
    log_file.write_text('[claude-code:unrecognized_model] {"model":"x"}\n'
                        '{"is_error": true, "api_error_status": 429, '
                        '"result": "You\'ve hit your 5-hour limit · resets 12:40am (Europe/Berlin)"}')
    with pytest.raises(lab.UsageLimit) as e:
        lab.check_usage(log_file)
    import datetime as dt
    at = lab.parse_reset(str(e.value), now=dt.datetime(2026, 11, 21, 23, 20))
    assert (at.day, at.hour, at.minute) == (22, 0, 40)            # after midnight = tomorrow
    assert lab.parse_reset("resets 3pm", now=dt.datetime(2026, 11, 21, 10, 0)).hour == 15
    log_file.write_text('warning line\n{"is_error": false, "result": "done, no limit problems"}')
    lab.check_usage(log_file)                                    # a normal result passes


def test_models_without_a_builtin_template_are_not_attempted():
    names = ["image_kandinsky5_t2i", "Kandinsky 5.0 Image Lite", "audio_ace_step1_5_xl_base", "ACE-Step",
             "sd3.5_simple_example", "video_hunyuan_video_1.5_720p_i2v", "image_firered_image_edit1_1"]
    yes = [("kandinsky-5", "Kandinsky 5"), ("ace-step-1.5", "ACE-Step 1.5 Music"), ("sd3.5", "Stable Diffusion 3.5"),
           ("hunyuan-video-1.5", "Hunyuan Video 1.5"), ("firered-image-edit-1.1", "FireRed Image Edit 1.1")]
    for slug, name in yes:
        assert lab.has_template(names, {"slug": slug, "model": name}), slug
    for slug in ("mistral-large-4-1t", "deepseek-v4.1-flash-1017", "nvidia-nemotron-3.5-bolt"):
        assert not lab.has_template(names, {"slug": slug, "model": slug}), slug
    assert lab.has_template([], {"slug": "anything"})            # no index to check against


def test_the_second_test_gets_the_same_prompts_models_and_inputs(tmp_path, monkeypatch):
    run, repo, inputs = tmp_path / "run", tmp_path / "repo", tmp_path / "in"
    (run / "final").mkdir(parents=True)
    (repo / "workflows" / "x").mkdir(parents=True)
    inputs.mkdir()
    (inputs / "portrait.png").write_bytes(b"x")
    (inputs / "unused.mp4").write_bytes(b"x")
    (run / "final" / "t2i.api.json").write_text(json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": "portrait.png"}}}))
    (repo / "workflows" / "x" / "models.json").write_text(json.dumps({"files": [
        {"name": "m.safetensors", "url": "https://hf/m", "dest": "vae", "required": True, "note": "1 GB"}]}))
    sent = {}
    monkeypatch.setattr(lab, "plutus", lambda cfg, tool, args=None, timeout=60: sent.update(args["params"]) or "ok")
    cfg = {"second_test_machine": "pc", "inputs_dir": str(inputs)}
    note = lab.queue_second_test(cfg, run, repo, {"folder": "workflows/x"}, "sdxl")
    payload = json.loads(sent["payload_json"])
    assert "queued for pc" in note and sent["machine"] == "pc" and sent["ref"] == "test-sdxl"
    assert payload["inputs"] == ["portrait.png"] and payload["models"][0]["name"] == "m.safetensors"
    assert payload["prompts"][0]["name"] == "t2i"
    assert lab.queue_second_test({"second_test_machine": "", "inputs_dir": ""}, run, repo, {}, "sdxl") == ""
