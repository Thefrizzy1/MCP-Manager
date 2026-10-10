"""Trend Radar: matching, scoring, the backtest, ideas and the Trends API. Offline."""
from __future__ import annotations

import io
import time
import zipfile

import pytest

from core import trend_radar as tr

DAY = 86400
FAMS = tr.DEFAULT_CONFIG["families"]


# ── matching ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,slug", [
    ("Wan-AI / Wan2.2-T2V-A14B", "wan-2.2"),
    ("[Pipeline] Add Wan 2.5 support", "wan-2.5"),
    ("Lightricks / LTX-2", "ltx-2"),
    ("HunyuanVideo-1.5 is out!", "hunyuanvideo-1.5"),
    ("FLUX.2 dev weights released", "flux-2"),
    ("Qwen Image Edit 2509 beats everything", "qwen-image"),     # 2509 is a date tag, not 1-2 digits
    ("Wan 14B on 8GB VRAM", "wan"),                              # 14B is a size, not a version
    ("LTXV 0.9.8 distilled", "ltxv-0.9.8"),
])
def test_match_finds_family_and_version(text, slug):
    assert tr.match(text, FAMS)[0] == slug


def test_match_returns_none_for_unrelated_text():
    assert tr.match("My cat learned to open doors", FAMS) is None
    assert tr.match("Swan lake ballet", FAMS) is None        # no family inside another word


def test_name_from_repo_strips_variant_noise():
    assert tr.name_from_repo("meituan-longcat/LongCat-Video-Base")[0] == "longcat-video"
    assert tr.name_from_repo("org/Kandinsky-5.0-T2V-Lite-sft-5s")[0].startswith("kandinsky-5.0")


# ── scoring ──────────────────────────────────────────────────────────────────

def sig(kind, ts, title="Wan 2.5", source="x", **metrics):
    return {"kind": kind, "ts": ts, "title": title, "source": source, "metrics": metrics}




@pytest.fixture
def conf(tmp_path):
    return tr.load_config(tmp_path)


def test_code_stage_is_prep_now(conf):
    now = int(time.time())
    s = [sig("code", now - 3600, "Add Wan 2.5 T2V 14B pipeline", "github:huggingface/diffusers"),
         sig("buzz", now - 7200, "Wan 2.5 incoming, insane", "reddit:StableDiffusion", score=900, comments=200),
         sig("repo", now - 5000, "Wan-AI / Wan2.5-T2V-14B", "hf:Wan-AI")]
    r = tr.score(s, now, conf, family="wan")
    assert r["stage"] == "code"
    assert r["action"] == "PREP NOW"
    assert r["hot"] >= 40
    assert r["parts"]["sources_confirming"] == 3


def test_saturation_turns_shoot_into_late_only_when_lowvram_is_taken_too(conf):
    now = int(time.time())
    base = [sig("weights", now - DAY, "Wan-AI / Wan2.5 video"), sig("gguf", now - DAY // 2, "Wan2.5 GGUF"),
            sig("buzz", now - DAY, "Wan 2.5 released, finally", "reddit:x", score=3000, comments=500)]
    generic = [sig("video", now - DAY // 4 - i, f"Wan 2.5 is INSANE #{i}", "youtube:c", views=50000) for i in range(8)]
    r1 = tr.score(base + generic, now, conf)
    assert r1["action"] in ("SHOOT: 4GB ANGLE", "WATCH")
    lowvram = [sig("video", now - DAY // 4 - i, f"Wan 2.5 on 4GB VRAM #{i}", "youtube:c", views=9000) for i in range(4)]
    r2 = tr.score(base + generic + lowvram, now, conf)
    assert r2["hot"] < r1["hot"]
    assert r2["action"] in ("LATE", "WATCH")


def test_score_ignores_the_future(conf):
    now = int(time.time())
    s = [sig("weights", now + DAY)]
    assert tr.score(s, now, conf)["hot"] == 0.0


def test_closed_family_has_lower_fit(conf):
    now = int(time.time())
    s = [sig("weights", now - 3600, "Kling 3 video")]
    assert tr.score(s, now, conf, family="kling")["parts"]["fit"] < tr.score(s, now, conf, family="wan")["parts"]["fit"]


def test_catches_raise_hot(conf):
    now = int(time.time())
    s = [sig("code", now - 3600, "Add Wan 2.5 video pipeline")]
    assert tr.score(s, now, conf, catches=3)["hot"] > tr.score(s, now, conf, catches=1)["hot"]


# ── backtest ─────────────────────────────────────────────────────────────────

def test_backtest_reports_lead_times(conf):
    t0 = 1_750_000_000
    s = [sig("paper", t0, "Wan 2.5 paper"),
         sig("code", t0 + 5 * DAY, "Add Wan 2.5 video pipeline", "github:diffusers"),
         sig("buzz", t0 + 5 * DAY, "Wan 2.5 incoming, huge", "reddit:x", score=2000, comments=300),
         sig("repo", t0 + 6 * DAY, "Wan-AI / Wan2.5 T2V 14B", "hf:Wan-AI"),
         sig("weights", t0 + 8 * DAY, "Wan-AI / Wan2.5 T2V 14B", "hf:Wan-AI", likes=900),
         sig("video", t0 + 9 * DAY, "Wan 2.5 first look", "youtube:other", views=40000),
         sig("own", t0 + 10 * DAY, "Wan 2.5 on 4GB", "youtube:me")]
    conf = {**conf, "alert_threshold": 45}
    r = tr.backtest(s, conf, family="wan", step_hours=6)
    assert r["ok"]
    assert r["release"] == t0 + 8 * DAY
    assert r["own_rank"] == 2
    assert r["alert"] is not None and r["alert"] <= r["release"]
    assert r["caught_before_release"]
    assert r["lead_alert_to_release_h"] > 0


# ── storage, ideas, done ─────────────────────────────────────────────────────

def test_ideas_merge_and_catches_feed_hot(tmp_path):
    conf = tr.load_config(tmp_path)
    now = int(time.time())
    fams = tr.known_families(tmp_path, conf)
    tr.add_signal(tmp_path, {"source": "github:d", "ext_id": "1", "kind": "code", "ts": now - 3600,
                             "title": "Add Wan 2.5 video pipeline"}, families=fams)
    tr.rescore(tmp_path, conf)
    hot1 = tr.get_candidate(tmp_path, "wan-2.5")["hot"]

    a = tr.save_idea(tmp_path, {"candidate": "wan-2.5", "title": "Wan 2.5 on a 4GB laptop", "angle": "4GB benchmark"})
    b = tr.save_idea(tmp_path, {"candidate": "wan-2.5", "title": "Can a 4GB laptop run Wan 2.5?", "angle": "4GB benchmark",
                                "script": "draft"})
    assert not a["merged"] and b["merged"] and b["id"] == a["id"]
    merged = tr.get_idea(tmp_path, a["id"])
    assert merged["catches"] == 2
    assert "Can a 4GB laptop run Wan 2.5?" in merged["alt_titles"]
    assert merged["script"] == "draft"

    other = tr.save_idea(tmp_path, {"candidate": "wan-2.5", "title": "Wan 2.5 camera control explained",
                                    "angle": "camera LoRA tutorial"})
    assert not other["merged"]

    tr.rescore(tmp_path, conf)
    assert tr.get_candidate(tmp_path, "wan-2.5")["hot"] > hot1


def test_save_idea_rejects_non_svg_and_unknown_model(tmp_path):
    with pytest.raises(ValueError):
        tr.save_idea(tmp_path, {"title": "x" * 5, "thumb_svg": "<script>alert(1)</script>"})
    with pytest.raises(KeyError):
        tr.save_idea(tmp_path, {"candidate": "nope-1", "title": "hello"})


def test_stale_ideas_archive_with_reason(tmp_path):
    i = tr.save_idea(tmp_path, {"title": "Old idea"})
    old = int(time.time()) - 30 * DAY
    with tr._connect(tmp_path) as con:
        con.execute("UPDATE ideas SET updated=? WHERE id=?", (old, i["id"]))
    assert tr.archive_stale_ideas(tmp_path) == 1
    assert tr.get_idea(tmp_path, i["id"])["archive_reason"].startswith("stale")
    shortlisted = tr.save_idea(tmp_path, {"title": "Keep me"})
    tr.set_idea(tmp_path, shortlisted["id"], {"status": "shortlisted"})
    with tr._connect(tmp_path) as con:
        con.execute("UPDATE ideas SET updated=? WHERE id=?", (old, shortlisted["id"]))
    tr.archive_stale_ideas(tmp_path)
    assert tr.get_idea(tmp_path, shortlisted["id"])["status"] == "shortlisted"


def test_done_suggestions_cross_reference_own_uploads(tmp_path):
    conf = tr.load_config(tmp_path)
    fams = tr.known_families(tmp_path, conf)
    now = int(time.time())
    tr.add_signal(tmp_path, {"source": "hf:Wan-AI", "ext_id": "w", "kind": "weights", "ts": now - 5 * DAY,
                             "title": "Wan-AI / Wan2.5 video"}, families=fams)
    idea = tr.save_idea(tmp_path, {"candidate": "wan-2.5", "title": "Wan 2.5 on 4GB"})
    loose = tr.save_idea(tmp_path, {"title": "SageAttention on a laptop GPU", "angle": "speed trick"})
    tr.add_signal(tmp_path, {"source": "youtube:me", "ext_id": "v1", "kind": "own", "ts": now,
                             "title": "I ran Wan 2.5 on a 4GB laptop", "url": "https://youtu.be/v1"}, families=fams)
    tr.add_signal(tmp_path, {"source": "youtube:me", "ext_id": "v2", "kind": "own", "ts": now,
                             "title": "SageAttention on my laptop GPU: worth it?"}, families=fams)
    sug = tr.suggest_done(tmp_path)
    ids = {s["idea_id"] for s in sug["ideas"]}
    assert {idea["id"], loose["id"]} <= ids
    assert [c["slug"] for c in sug["candidates"]] == ["wan-2.5"]
    res = tr.apply_done(tmp_path, [idea["id"]], [])
    assert res == {"ideas": 1, "candidates": 0}
    assert tr.get_idea(tmp_path, idea["id"])["status"] == "published"
    assert tr.apply_done(tmp_path)["ideas"] == 1           # the rest
    fin = tr.finished(tmp_path)
    assert len(fin["ideas"]) == 2


def test_partial_config_save_keeps_other_fields(tmp_path):
    tr.save_config(tmp_path, {"subreddits": ["comfyui"]})
    tr.save_config(tmp_path, {"alert_threshold": 60})
    c = tr.load_config(tmp_path)
    assert c["subreddits"] == ["comfyui"] and c["alert_threshold"] == 60
    with pytest.raises(ValueError):
        tr.save_config(tmp_path, {"github_repos": ["not a repo"]})
    with pytest.raises(ValueError):
        tr.save_config(tmp_path, {"nonsense": 1})


def test_skill_install_confines_paths(tmp_path):
    from ui.api.trends import skill_files_from_upload

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("frizzy-youtube/SKILL.md", "---\nname: frizzy-youtube\ndescription: voice\n---\nbody")
        z.writestr("frizzy-youtube/references/voice.md", "samples")
    name, files = skill_files_from_upload("frizzy-youtube.skill", buf.getvalue())
    assert name == "frizzy-youtube" and set(files) == {"SKILL.md", "references/voice.md"}
    s = tr.install_skill(tmp_path, name, files)
    assert s["files"] == 2 and s["description"] == "voice"
    with pytest.raises(ValueError):
        tr.install_skill(tmp_path, "evil", {"SKILL.md": b"x", "../../etc/x": b"y"})
    with pytest.raises(ValueError):
        tr.install_skill(tmp_path, "../up", {"SKILL.md": b"x"})


# ── collectors' parsers ──────────────────────────────────────────────────────

def test_hf_signal_classifies_stage():
    from tools.trend_radar import hf_signal
    m = {"id": "Wan-AI/Wan2.5-T2V", "pipeline_tag": "text-to-video", "createdAt": "2026-09-01T00:00:00.000Z",
         "siblings": [{"rfilename": "model.safetensors"}], "likes": 5}
    assert hf_signal(m, source="hf:Wan-AI")["kind"] == "weights"
    assert hf_signal({**m, "siblings": []}, source="hf:Wan-AI")["kind"] == "repo"
    assert hf_signal({"id": "Qwen/Qwen3-8B", "pipeline_tag": "text-generation"}, source="hf:Qwen") is None
    q = hf_signal({"id": "QuantStack/Wan2.5-GGUF", "siblings": [{"rfilename": "a.gguf"}]}, source="hfq:QuantStack", quant=True)
    assert q["kind"] == "gguf"


def test_comfy_history_outcome():
    from tools.trend_radar import history_outcome
    ok = {"status": {"status_str": "success", "messages": [
        ["execution_start", {"timestamp": 1000}], ["execution_success", {"timestamp": 61000}]]}}
    assert history_outcome(ok) == ("ok", 60.0, "")
    oom = {"status": {"status_str": "error", "messages": [
        ["execution_start", {"timestamp": 0}],
        ["execution_error", {"timestamp": 5000, "node_type": "KSampler",
                             "exception_message": "CUDA out of memory. Tried to allocate"}]]}}
    assert history_outcome(oom)[0] == "oom"


def test_atom_and_pattern_report():
    from tools.trend_radar import atom_signals, pattern_report
    xml = ("<feed><entry><id>t3_abc</id><title>Wan 2.5 &amp; friends</title>"
           "<updated>2026-09-01T10:00:00+00:00</updated><link href=\"https://r/x\"/></entry></feed>")
    s = atom_signals(xml, source="reddit:x", kind="buzz")
    assert s[0]["title"] == "Wan 2.5 & friends" and s[0]["ts"] > 0
    assert "too few" in pattern_report([{"title": "a", "vpd": 1}])[0]


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ui.api import trends as T
    from ui.api.deps import verify_auth

    monkeypatch.setattr(T, "ROOT", tmp_path)
    monkeypatch.setattr(T, "sync_schedules", lambda conf: {})
    monkeypatch.setattr(T, "_schedule_info", lambda: {"collect": None, "studio": None})
    app = FastAPI()
    app.include_router(T.router)
    app.dependency_overrides[verify_auth] = lambda: None
    return TestClient(app)


def test_api_overview_ideas_thumb_and_done(client, tmp_path):
    conf = tr.load_config(tmp_path)
    tr.add_signal(tmp_path, {"source": "github:d", "ext_id": "1", "kind": "code", "ts": int(time.time()),
                             "title": "Add LTX 2.5 pipeline"}, families=tr.known_families(tmp_path, conf))
    tr.rescore(tmp_path, conf)
    ov = client.get("/api/v1/trends/overview").json()
    assert ov["candidates"][0]["slug"] == "ltx-2.5"

    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720"/></svg>'
    i = tr.save_idea(tmp_path, {"candidate": "ltx-2.5", "title": "LTX 2.5 on 4GB", "thumb_svg": svg})
    r = client.get(f"/api/v1/trends/ideas/{i['id']}/thumb.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in r.headers["content-security-policy"]

    r = client.post(f"/api/v1/trends/ideas/{i['id']}", json={"status": "shortlisted"})
    assert r.json()["idea"]["status"] == "shortlisted"
    assert client.post(f"/api/v1/trends/ideas/{i['id']}", json={"hot": 99}).status_code == 400

    d = client.get("/api/v1/trends/done").json()
    assert d["suggestions"]["ideas"] == []
    assert client.post("/api/v1/trends/done/apply", json={}).json()["ok"]


def test_api_config_validates(client):
    assert client.post("/api/v1/trends/config", json={"alert_threshold": 500}).status_code == 400
    assert client.post("/api/v1/trends/config", json={"schedule_cron": "not cron"}).status_code == 400
    ok = client.post("/api/v1/trends/config", json={"subreddits": ["comfyui", "StableDiffusion"]})
    assert ok.status_code == 200 and ok.json()["config"]["subreddits"] == ["comfyui", "StableDiffusion"]


def test_studio_skill_upload_endpoint(client, tmp_path):
    files = {"file": ("SKILL.md", b"---\nname: thumbnail\ndescription: thumbs\n---\n", "text/markdown")}
    r = client.post("/api/v1/trends/studio/skills", files=files)
    assert r.status_code == 200 and r.json()["skill"]["name"] == "thumbnail"
    assert (tmp_path / "data" / tr.STUDIO_DIR / ".claude" / "skills" / "thumbnail" / "SKILL.md").exists()
    st = client.get("/api/v1/trends/studio").json()
    assert st["skills"][0]["name"] == "thumbnail" and "trend_radar_save_idea" in st["prompt"]


def test_idea_similarity_merges_same_angle_not_same_model_only():
    a = {"candidate": "ltx-2.3", "title": "Lightricks Says LTX 2.3 Needs 32GB. My Laptop Has 4.",
         "angle": "4GB benchmark day one"}
    b = {"candidate": "ltx-2.3", "title": "Can a 4GB laptop run LTX 2.3?", "angle": "4GB benchmark"}
    c = {"candidate": "ltx-2.3", "title": "LTX 2.3 camera control explained", "angle": "camera LoRA tutorial"}
    d = {"candidate": "ltx-2.3", "title": "LTX 2.3 is here"}
    assert tr.idea_similarity(a, b) >= tr.MERGE_AT
    assert tr.idea_similarity(a, c) < tr.MERGE_AT
    assert tr.idea_similarity(c, d) < tr.MERGE_AT          # sharing only the model name is not a duplicate


# ── matcher v2: real titles from a live collect ──────────────────────────────

def test_subject_and_mentions_from_real_titles():
    fams = tr.load_config.__globals__["DEFAULT_CONFIG"]["families"]
    m = tr.match_all("Krea 2 Beats Z Image & Boogu Image? Full ComfyUI Comparison & Workflow", fams)
    assert m[0][0] == "krea-2" and {x[0] for x in m[1:]} == {"z-image", "boogu"}
    m = tr.match_all("Forget Z-Image! Boogu Image Just Changed Everything (ComfyUI Workflow)", fams)
    assert m[0][0] == "boogu" and m[1][0] == "z-image"            # "Forget X" makes X the foil
    m = tr.match_all("ComfyUI INT8 Performance Boost! Boogu, Krea2 & Z-Image (Ep25)", fams)
    assert {x[0] for x in m} == {"boogu", "krea-2", "z-image"}
    assert tr.match("Wan-AI / Wan2.2-T2V-A14B", fams)[0] == "wan-2.2"
    assert [x[0] for x in tr.match_all("HunyuanVideo 1.5 vs Hunyuan", fams)] == ["hunyuanvideo-1.5", "hunyuan"]


def test_pr_body_never_matches_and_titles_teach_new_models(tmp_path):
    conf = tr.load_config(tmp_path)
    fams = tr.known_families(tmp_path, conf)
    pr = {"source": "github:huggingface/diffusers", "ext_id": "14991", "kind": "code", "ts": 1,
          "title": "Add Bria fibo-2", "match_text": "Follows the Z-Image pipeline layout"}
    assert tr.add_signal(tmp_path, pr, families=fams, allow_new_family=True) == "fibo-2"
    old = {"source": "github:city96/ComfyUI-GGUF", "ext_id": "425", "kind": "code", "ts": 1,
           "title": "Enhance loader compatibility for sdcpp exports", "match_text": "tested with Z-Image"}
    assert tr.add_signal(tmp_path, old, families=fams, allow_new_family=True) == ""
    assert tr.name_from_title("[Pipeline] Add Boogu Image") == ("boogu-image", "Boogu Image")
    for title in ("Add LoRA support for Wan", "Add tests for flux", "Support FP8 on MPS", "Fix typo"):
        assert tr.name_from_title(title) is None, title


def test_mention_counts_only_as_coverage(tmp_path):
    conf = tr.load_config(tmp_path)
    fams = tr.known_families(tmp_path, conf)
    now = int(time.time())
    tr.add_signal(tmp_path, {"source": "hf:alibaba-pai", "ext_id": "z", "kind": "weights", "ts": now - 86400,
                             "title": "alibaba-pai / Z-Image-Fun-Lora-Distill"}, families=fams)
    tr.add_signal(tmp_path, {"source": "youtube:@sotaihub", "ext_id": "v", "kind": "video", "ts": now - 3600,
                             "title": "Krea 2 Beats Z Image & Boogu Image?", "metrics": {"views": 1000}},
                  families=fams)
    tr.add_signal(tmp_path, {"source": "github:huggingface/diffusers", "ext_id": "p", "kind": "code",
                             "ts": now - 600, "title": "Add Krea 2 vs Z-Image docs"}, families=fams)
    tr.rescore(tmp_path, conf)
    z = {s["ext_id"]: s["role"] for s in tr.signals_for(tmp_path, "z-image")}
    assert z == {"z": "subject", "v": "mention", "p": "mention"}
    assert [s["ext_id"] for s in tr.signals_for(tmp_path, "krea-2") if s["role"] == "subject"] == ["v", "p"]
    parts = tr.get_candidate(tmp_path, "z-image")["score"]
    assert parts["creator_videos_10d"] == 1                 # the comparison video is coverage for Z-Image
    assert tr.get_candidate(tmp_path, "z-image")["stage"] == "weights"   # the Krea PR did not move it


def test_rematch_fixes_old_data_and_keeps_merges(tmp_path):
    conf = tr.load_config(tmp_path)
    with tr._connect(tmp_path) as con:                      # rows as matcher v1 filed them
        for eid, title, cand in [("14991", "Add Bria fibo-2", "z-image"),
                                 ("v", "Krea 2 Beats Z Image & Boogu Image?", "z-image"),
                                 ("w", "LTXV 2 is out", "ltxv-2")]:
            con.execute("INSERT INTO signals (source, ext_id, kind, title, ts, seen, candidate) VALUES (?,?,?,?,?,?,?)",
                        ("github:huggingface/diffusers" if eid == "14991" else "youtube:x", eid,
                         "code" if eid == "14991" else "video", title, 1, 1, cand))
        for slug, fam in (("z-image", "z-image"), ("ltxv-2", "ltxv"), ("ltx-2", "ltx")):
            con.execute("INSERT INTO candidates (slug, name, family, first_ts) VALUES (?,?,?,1)", (slug, slug, fam))
    tr.merge(tmp_path, "ltxv-2", "ltx-2")
    assert tr.matcher_outdated(tmp_path)
    res = tr.rematch_all(tmp_path, conf)
    by = {s["ext_id"]: s for s in tr.recent_signals(tmp_path)}
    assert by["14991"]["candidate"] == "fibo-2" and by["v"]["candidate"] == "krea-2"
    assert set(by["v"]["mentions"].split(",")) == {"z-image", "boogu"}
    assert by["w"]["candidate"] == "ltx-2"                  # merge survived the rematch
    assert not tr.matcher_outdated(tmp_path) and res["moved"] >= 2


# ── Idea Studio works on runtimes that don't read the folder (Codex) ─────────

def test_studio_context_inlines_brief_and_skills(tmp_path):
    tr.save_studio_brief(tmp_path, "# Brief\nNo em dashes.")
    tr.install_skill(tmp_path, "thumbnail", {"SKILL.md": b"---\nname: thumbnail\n---\nDark editorial.",
                                             "references/style.md": b"yellow accent"})
    tr.save_knowledge(tmp_path, "analytics.csv", b"video,ctr\n")
    p = tr.studio_prompt(tr.load_config(tmp_path), tmp_path)
    assert "No em dashes." in p and "Dark editorial." in p
    assert ".claude/skills/thumbnail/references/style.md" in p and "knowledge/analytics.csv" in p
    assert p.index("Dark editorial.") < p.index("trend_radar_ideas")        # context first, then the task
    assert tr.read_studio_file(tmp_path, ".claude/skills/thumbnail/references/style.md") == "yellow accent"
    with pytest.raises(FileNotFoundError):
        tr.read_studio_file(tmp_path, "../../.env")


def test_studio_runs_are_scoped_and_schedule_builds_prompt_at_fire_time(tmp_path, monkeypatch):
    from ui.api import trends as T
    import ui.runtime as rt

    monkeypatch.setattr(T, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(rt, "_run_agent_bg", lambda prompt, label, **kw: calls.append((prompt, label, kw)))
    T._launch_explainer(tr.studio_prompt(tr.load_config(tmp_path), tmp_path), "sched:Idea Studio")
    prompt, _, kw = calls[0]
    assert "trend_radar" in kw["mcp_services"] and "jellyfin" not in kw["mcp_services"]
    assert kw["cwd"].endswith("trend_radar_studio") and "Channel brief" in prompt
    denied = set(rt._agent_service_disallow(kw["mcp_services"]))
    assert not any(t.startswith("mcp__plutus__trend_radar_") for t in denied)
    assert "mcp__plutus__jellyfin_search" in denied

    monkeypatch.setattr(T, "_schedule_info", lambda: {})
    import core.schedule_store as ss
    store = {}
    monkeypatch.setattr(ss, "load_schedules", lambda root: list(store.values()))
    monkeypatch.setattr(ss, "add_schedule", lambda root, e: store.setdefault(e["payload"]["origin"], {**e, "id": "x"}))
    T.sync_schedules(tr.load_config(tmp_path))
    studio = store[T.STUDIO_ORIGIN]
    assert studio["kind"] == "tool" and studio["payload"]["tool"] == "trend_radar_studio_run"
