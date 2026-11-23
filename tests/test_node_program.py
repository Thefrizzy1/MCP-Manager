"""The node program: the pieces that decide and guard, without a real machine."""
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location("plutus_node", Path(__file__).parents[1] / "node" / "plutus_node.py")
node = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(node)

CFG = dict(node.DEFAULTS, machine="pc", kinds=["comfy_test"], report=False, idle_minutes=15)


def _server(monkeypatch, answer, calls):
    def fake(cfg, tool, args=None, timeout=40):
        calls.append((tool, args))
        return json.dumps(answer) if tool == "node_next_job" else "ok"
    monkeypatch.setattr(node, "plutus", fake)


def test_magic_packet_is_six_ff_then_the_mac_sixteen_times():
    p = node.magic_packet("aa-bb-cc-dd-ee-ff")
    assert p[:6] == b"\xff" * 6 and p[6:] == bytes.fromhex("aabbccddeeff") * 16 and len(p) == 102


def test_power_off_needs_the_server_and_an_idle_machine(monkeypatch):
    calls = []
    _server(monkeypatch, {"job": None, "may_power_off": True}, calls)
    monkeypatch.setattr(node, "idle_seconds", lambda: 60.0)             # someone was just here
    assert node.tick(CFG) == "idle"
    monkeypatch.setattr(node, "idle_seconds", lambda: 3600.0)
    assert node.tick(CFG) == "poweroff"
    _server(monkeypatch, {"job": None, "may_power_off": False}, calls)
    assert node.tick(CFG) == "idle"                                     # a person switched it on


def test_a_job_of_a_kind_this_node_does_not_do_is_refused_not_run(monkeypatch):
    calls = []
    _server(monkeypatch, {"job": {"id": 7, "kind": "wake", "payload": {"mac": "aa:bb:cc:dd:ee:ff"}}}, calls)
    sent = []
    monkeypatch.setitem(node.HANDLERS, "wake", lambda cfg, p: sent.append(p) or {"sent": True})
    assert node.tick(CFG) == "job" and sent == []                       # this node only does comfy_test
    done = [a for t, a in calls if t == "node_job_done"][0]
    assert done["ok"] is False and "does not do" in done["result_json"]
    assert node.tick(dict(CFG, kinds=["wake"])) == "job" and len(sent) == 1


def test_a_failed_run_reports_failure(monkeypatch):
    calls = []
    _server(monkeypatch, {"job": {"id": 8, "kind": "comfy_test", "payload": {"slug": "sdxl"}}}, calls)
    monkeypatch.setitem(node.HANDLERS, "comfy_test",
                        lambda cfg, p: {"ok": False, "runs": [{"name": "t2i", "ok": False, "error": "out of memory"}]})
    node.tick(CFG)
    done = [a for t, a in calls if t == "node_job_done"][0]
    assert done["ok"] is False and "out of memory" in done["result_json"]


def test_a_model_found_in_a_subfolder_is_asked_for_by_its_subfolder_path(tmp_path, monkeypatch):
    comfy = tmp_path / "ComfyUI"
    (comfy / "models" / "vae" / "Krea-2").mkdir(parents=True)
    (comfy / "models" / "vae" / "Krea-2" / "qwen_image_vae.safetensors").write_bytes(b"x")
    (comfy / "input").mkdir()
    sent = []
    monkeypatch.setattr(node, "start_comfy", lambda cfg: None)
    monkeypatch.setattr(node, "download", lambda url, dest: (_ for _ in ()).throw(AssertionError("must not download")))
    class Resp:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_open(req, timeout=0):
        sent.append(json.loads(req.data.decode())["prompt"])
        return Resp(b'{"prompt_id": "p1"}')
    monkeypatch.setattr(node.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(node, "comfy_get", lambda cfg, path, timeout=10: {"p1": {"status": {"status_str": "success"}}})
    monkeypatch.setattr(node, "gpu_now", lambda: {})
    payload = {"slug": "anima", "models": [{"name": "qwen_image_vae.safetensors", "dest": "vae", "url": "u", "required": True}],
               "prompts": [{"name": "s1", "prompt": {"10": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
                                                    "11": {"class_type": "X", "inputs": {"steps": 30, "other": "keep"}}}}]}
    res = node.do_comfy_test(dict(node.DEFAULTS, comfy_dir=str(comfy)), payload)
    expected = ("Krea-2\\" if node.WIN else "Krea-2/") + "qwen_image_vae.safetensors"
    assert sent[0]["10"]["inputs"]["vae_name"] == expected and sent[0]["11"]["inputs"] == {"steps": 30, "other": "keep"}
    assert res["ok"] and res["renamed"] == {"qwen_image_vae.safetensors": expected}
