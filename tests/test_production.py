"""Productions (explainers), benchmarks comparisons, VibeVoice plumbing. Offline."""
from __future__ import annotations

import io
import json
import struct
import time
import zipfile

import pytest

from core import production as pr
from core import trend_radar as tr


def _scenes(bench_ids=None):
    s = [{"id": "intro", "chapter": "Intro", "narration": "Wan 2.5 says 24 GB. I have four.",
          "visual": {"type": "title", "heading": "4GB?", "caption": "Wan 2.5"}},
         {"id": "steps", "chapter": "Install", "narration": "Three files. That is the whole install.",
          "visual": {"type": "bullets", "lines": ["UNet", "Text encoder", "VAE"]},
          "direction": "Show the three model folders"}]
    if bench_ids:
        s.append({"id": "bench", "chapter": "Performance", "narration": "Here is the part you came for.",
                  "visual": {"type": "comparison", "heading": "Sage off vs on", "bench_ids": bench_ids}})
    return s


def test_time_parse_and_format():
    assert pr.parse_time("1:06:00") == 3960 and pr.parse_time("54:48") == 3288 and pr.parse_time(90) == 90
    assert pr.fmt_time(3960) == "1:06:00" and pr.fmt_time(65) == "1:05"
    with pytest.raises(ValueError):
        pr.parse_time("1h")


def test_manual_benchmarks_compare_and_export(tmp_path):
    a = pr.add_manual_benchmark(tmp_path, {"slug": "ltx-2.3", "label": "Baseline dev", "time": "1:06:00",
                                           "hw": "RTX 3050 Ti 4GB"})
    b = pr.add_manual_benchmark(tmp_path, {"slug": "ltx-2.3", "label": "Distilled 8-step", "time": "40:22",
                                           "mark": True})
    oom = pr.add_manual_benchmark(tmp_path, {"slug": "ltx-2.3", "label": "torch.compile", "status": "oom"})
    with pytest.raises(ValueError):
        pr.add_manual_benchmark(tmp_path, {"label": "x"})            # ok without a time
    c = pr.compare(a, b)
    assert c["faster"] and c["pct"] == 39 and c["saved_seconds"] == 1538
    doc = pr.studioforge_graphics([a, b, oom], heading="LTX 2.3 render time", pair=(a, b))
    ids = [g["id"] for g in doc["graphics"]]
    assert ids == ["benchmark", "comparison"]
    assert doc["graphics"][0]["props"]["runs"][1] == {"label": "Distilled 8-step", "time": "40:22", "accent": True}
    assert doc["graphics"][1]["props"]["delta"]["faster"] is True
    table = pr.notes_table([a, b])
    assert "| gguf | Baseline dev |  | 1:06:00 |  |" in table


def test_notes_table_import_roundtrip(tmp_path):
    text = ("| group | label | file | time | mark |\n|---|---|---|---|---|\n"
            "| gguf | Q3_K_M · 5 s | 0002 | 50:19 |  |\n| gguf | Q6_K · Sage | 0007 | 42:20 | x |\n")
    assert pr.import_notes_table(tmp_path, text, slug="minimax-h3") == 2
    rows = pr.all_benchmarks(tmp_path, slug="minimax-h3")
    assert [r["time"] for r in rows] == ["42:20", "50:19"] and rows[0]["mark"] == 1


def test_production_validation(tmp_path):
    with pytest.raises(ValueError):
        pr.save_production(tmp_path, {"title": "x", "scenes": []})
    with pytest.raises(ValueError):      # numbers never typed into a benchmark scene
        pr.save_production(tmp_path, {"title": "x", "scenes": [
            {"narration": "fast", "visual": {"type": "benchmark", "runs": [{"time": "1:00"}]}}]})
    with pytest.raises(ValueError):
        pr.save_production(tmp_path, {"title": "x", "scenes": [{"narration": "a", "speaker": 5}]})
    with pytest.raises(ValueError):
        pr.save_production(tmp_path, {"title": "x", "scenes": [{"id": "../x", "narration": "a"}]})


def test_production_scripts_and_audio_kept_on_resave(tmp_path):
    a = pr.add_manual_benchmark(tmp_path, {"label": "Without Sage", "time": "1:06:00"})
    b = pr.add_manual_benchmark(tmp_path, {"label": "With Sage", "time": "1:31:16"})
    p = pr.save_production(tmp_path, {"title": "LTX 2.3 on 4GB", "scenes": _scenes([a["id"], b["id"]])})
    assert p["script_md"].startswith("# LTX 2.3 on 4GB\n\n## Intro")
    assert "_(Show the three model folders)_" in p["script_md"]
    assert pr.voice_script(p["scenes"]).splitlines()[0] == "[1]: Wan 2.5 says 24 GB. I have four."
    pr.set_scene_audio(tmp_path, p["id"], "intro", {"file": "intro.flac", "seconds": 3.2})
    scenes = _scenes([a["id"], b["id"]])
    scenes[1]["narration"] = "Changed line."
    p2 = pr.save_production(tmp_path, {"title": "LTX 2.3 on 4GB", "scenes": scenes}, p["id"])
    by = {s["id"]: s for s in p2["scenes"]}
    assert by["intro"]["audio"]["seconds"] == 3.2 and "audio" not in by["steps"]
    data = pr.remotion_data(tmp_path, p2)
    cmp_scene = data["scenes"][2]["visual"]
    assert [r["time"] for r in cmp_scene["runs"]] == ["1:06:00", "1:31:16"]
    assert cmp_scene["delta"]["faster"] is False                    # Sage made it slower: honest red
    assert data["scenes"][0]["seconds"] == pytest.approx(3.7)


def _wav(seconds: float, rate: int = 24000) -> bytes:
    n = int(seconds * rate)
    body = b"\x00\x00" * n
    return (b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(body)) + body)


def _flac_header(seconds: float, rate: int = 24000) -> bytes:
    total = int(seconds * rate)
    packed = (rate << 44) | (0 << 41) | (15 << 36) | total
    info = b"\x00" * 10 + packed.to_bytes(8, "big") + b"\x00" * 16
    return b"fLaC" + b"\x80\x00\x00\x22" + info


def test_audio_seconds_from_headers():
    assert pr.audio_seconds(_wav(2.5)) == pytest.approx(2.5)
    assert pr.audio_seconds(_flac_header(7.25)) == pytest.approx(7.25)
    assert pr.audio_seconds(b"ID3....") is None


def test_package_has_studioforge_files(tmp_path):
    a = pr.add_manual_benchmark(tmp_path, {"label": "A", "time": "10:00"})
    b = pr.add_manual_benchmark(tmp_path, {"label": "B", "time": "8:00"})
    p = pr.save_production(tmp_path, {"title": "Test Video", "scenes": _scenes([a["id"], b["id"]])})
    (pr.prod_dir(tmp_path, p["id"]) / "audio" / "intro.wav").write_bytes(_wav(1.0))
    data, name = pr.build_package(tmp_path, p["id"])
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    for f in ("script.md", "voice/voice_script.txt", "voice/intro.wav", "benchmarks.md", "studioforge_import.json",
              "remotion/package.json", "remotion/src/Explainer.tsx", "remotion/src/data.json",
              "remotion/public/audio/intro.wav", "README.md"):
        assert f"test-video/{f}" in names, f
    imp = json.loads(z.read("test-video/studioforge_import.json"))
    assert imp["graphics"][1]["props"]["delta"]["faster"] is True
    assert not any("node_modules" in n for n in names)


def test_reference_structure_gives_shape_not_words():
    video = {"id": "abc", "title": "Ref", "duration": 600, "views": 1000,
             "chapters": [{"title": "Hook", "start": 0, "end": 30}, {"title": "Setup", "start": 30, "end": 300},
                          {"title": "Results", "start": 300, "end": 600}],
             "heatmap": [{"start": i * 10, "end": i * 10 + 10, "value": 1.0 if i == 40 else 0.1} for i in range(60)]}
    segs = [(float(i * 4), "word " * 10) for i in range(150)]
    r = pr.reference_structure(video, segs)
    assert r["chapter_count"] == 3 and r["avg_chapter_seconds"] == 200
    assert r["wpm"] and 140 <= r["wpm"] <= 160
    assert r["most_replayed"][0]["chapter"] == "Results"
    assert "transcript" not in json.dumps(r).lower()


# ── tools side ───────────────────────────────────────────────────────────────

def test_voice_template_fill_escapes_text():
    from tools.production import fill_voice
    tpl = {"5": {"class_type": "VibeVoiceMultipleSpeakersNode", "inputs": {"text": "{{line}}", "seed": "{{seed}}"}},
           "6": {"class_type": "Other", "inputs": {"t": "{{text}}"}}}
    out = fill_voice(tpl, text='He said "4 GB"\nand left', speaker=2, seed=7)
    assert out["5"]["inputs"]["text"] == '[2]: He said "4 GB"\nand left'
    assert out["5"]["inputs"]["seed"] == 7
    assert out["6"]["inputs"]["t"].startswith("He said")


def test_audio_outputs_found_in_any_node():
    from tools.production import audio_outputs
    entry = {"outputs": {"9": {"audio": [{"filename": "vo_00001_.flac", "subfolder": "audio", "type": "output"}]},
                         "3": {"images": [{"filename": "x.png"}]}}}
    assert [f["filename"] for f in audio_outputs(entry)] == ["vo_00001_.flac"]


def test_magic_packet():
    from tools.production import magic_packet
    p = magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(p) == 102 and p[:6] == b"\xff" * 6 and p[6:12] == bytes.fromhex("AABBCCDDEEFF")


def test_config_validates_voice_and_wake(tmp_path):
    with pytest.raises(ValueError):
        tr.save_config(tmp_path, {"voice": {"template": {"1": {"inputs": {"text": "hello"}}}}})
    with pytest.raises(ValueError):
        tr.save_config(tmp_path, {"wake": {"mac": "nope"}})
    with pytest.raises(ValueError):
        tr.save_config(tmp_path, {"auto": {"cron": "bad"}})
    c = tr.save_config(tmp_path, {"auto": {"enabled": True}})
    assert c["auto"]["enabled"] and c["auto"]["cron"] == "0 2 * * *" and c["auto"]["voice"] is True


def test_auto_run_is_off_by_default(tmp_path):
    import asyncio
    from tools.production import auto_run
    assert asyncio.run(auto_run(tmp_path)) == {"skipped": "auto mode is off"}


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ui.api import trends as T
    from ui.api.deps import verify_auth

    monkeypatch.setattr(T, "ROOT", tmp_path)
    app = FastAPI()
    app.include_router(T.router)
    app.dependency_overrides[verify_auth] = lambda: None
    return TestClient(app)


def test_api_benchmarks_and_production_flow(client, tmp_path):
    r = client.post("/api/v1/trends/benchmarks", json={"label": "Base", "time": "1:06:00", "slug": "ltx-2.3"})
    a = r.json()["benchmark"]["id"]
    b = client.post("/api/v1/trends/benchmarks", json={"label": "Distilled", "time": "40:22"}).json()["benchmark"]["id"]
    assert client.post("/api/v1/trends/benchmarks", json={"label": "x", "time": "soon"}).status_code == 400
    ex = client.post("/api/v1/trends/benchmarks/export", json={"ids": [a, b], "pair": [a, b]}).json()
    assert ex["comparison"]["pct"] == 39 and len(ex["studioforge"]["graphics"]) == 2

    p = client.post("/api/v1/trends/productions", json={"title": "LTX on 4GB", "scenes": _scenes([a, b])}).json()
    pid = p["production"]["id"]
    full = client.get(f"/api/v1/trends/productions/{pid}").json()
    assert full["voice_script"].startswith("[1]:") and set(full["bench"]) == {str(a), str(b)}
    assert client.post(f"/api/v1/trends/productions/{pid}/auto", json={"on": True}).json()["production"]["auto"] == 1
    dl = client.get(f"/api/v1/trends/productions/{pid}/download")
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
    assert client.get(f"/api/v1/trends/productions/{pid}/audio/..%2F..%2Fsecrets").status_code == 404
    assert client.delete(f"/api/v1/trends/productions/{pid}").json()["ok"]
