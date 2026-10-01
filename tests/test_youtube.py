"""YouTube research, tracking and the owner-only Google tools — offline.

Every network seam is stubbed: yt-dlp's page reads, the Data API, Google's token
endpoint, the Analytics API. What is under test is what Plutus does with the
answers — which caption track it picks, what a delta is computed from, that a
token never reaches the browser, that the OAuth callback only finishes a sign-in
this process started. One ``live`` test at the bottom proves the transcript path
against YouTube itself.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from config import cfg
from core import google_oauth as go
from core import youtube_reporting as yr
from core import youtube_scrape as ys
from core import youtube_store as store


def _tools(module_register):
    from mcp.server.fastmcp import FastMCP
    m = FastMCP("t")
    module_register(m)
    return {t.name: t for t in m._tool_manager.list_tools()}


def _run(tool, payload):
    from core.invoke_tool import invoke_mcp_tool_fn
    return str(asyncio.run(invoke_mcp_tool_fn(tool.fn, payload=payload)))


# ── argument parsing ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ref", [
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    "https://youtu.be/dQw4w9WgXcQ?si=abc",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://m.youtube.com/live/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
])
def test_video_ids_are_read_from_every_url_shape(ref):
    assert ys.parse_video_id(ref) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("ref", ["", "nope", "https://evil.example/watch?v=dQw4w9WgXcQ"])
def test_a_video_id_on_another_host_is_refused(ref):
    """The id pattern alone would accept evil.example/watch?v=… — the host check
    is what keeps a model-supplied URL from being passed on as if it were YouTube."""
    with pytest.raises(ValueError):
        ys.parse_video_id(ref)


def test_channel_refs_become_youtube_urls_only():
    assert ys.channel_url("@the_frizzy1", "videos") == "https://www.youtube.com/@the_frizzy1/videos"
    assert ys.channel_url("the_frizzy1") == "https://www.youtube.com/@the_frizzy1"
    cid = "UCGKDBMhTGqvlBZE9cF5d50Q"
    assert ys.channel_url(cid, "shorts") == f"https://www.youtube.com/channel/{cid}/shorts"
    assert ys.channel_url("https://www.youtube.com/@x_y/videos?view=0", "streams") == \
        "https://www.youtube.com/@x_y/streams"
    with pytest.raises(ValueError):
        ys.channel_url("https://169.254.169.254/latest/meta-data", "videos")
    with pytest.raises(ValueError):
        ys.channel_url("has spaces and / slashes")


def test_yt_dlp_is_pinned_to_its_youtube_extractors():
    """yt-dlp's generic extractor fetches any URL it is given. Pinning it to the
    YouTube extractors is what stops a tool argument from becoming an arbitrary
    fetch — and it is decided before any request is made, so this runs offline."""
    pytest.importorskip("yt_dlp")
    assert ys._opts()["allowed_extractors"] == ["youtube.*"]
    with pytest.raises(ys.ScrapeError, match="extractor"):
        ys._extract("https://example.com/")


def test_channel_params_for_the_data_api():
    from tools.youtube import channel_params
    assert channel_params("@the_frizzy1") == {"forHandle": "@the_frizzy1"}
    assert channel_params("the_frizzy1") == {"forHandle": "@the_frizzy1"}
    assert channel_params("UCGKDBMhTGqvlBZE9cF5d50Q") == {"id": "UCGKDBMhTGqvlBZE9cF5d50Q"}
    assert channel_params("https://www.youtube.com/channel/UCGKDBMhTGqvlBZE9cF5d50Q/videos") == \
        {"id": "UCGKDBMhTGqvlBZE9cF5d50Q"}
    assert channel_params("https://www.youtube.com/@abc/shorts") == {"forHandle": "@abc"}


# ── transcripts ──────────────────────────────────────────────────────────────

def _track(url):
    return [{"ext": "srv3", "url": url + "&fmt=srv3"}, {"ext": "json3", "url": url}]


def test_the_creators_subtitles_win_over_auto_captions():
    t = ys.pick_caption_track({"en": _track("manual-en")},
                              {"en-orig": _track("auto-en"), "de": _track("auto-de")},
                              spoken="en-US")
    assert (t["kind"], t["lang"], t["url"]) == ("manual", "en", "manual-en")


def test_without_subtitles_the_spoken_language_asr_is_used_not_a_translation():
    auto = {"de": _track("auto-de"), "en": _track("auto-en-translated"), "en-orig": _track("auto-en")}
    t = ys.pick_caption_track({}, auto, spoken="en")
    assert (t["kind"], t["url"], t["translated"]) == ("auto", "auto-en", False)


def test_asking_for_another_language_says_it_is_machine_translated():
    auto = {"de": _track("auto-de"), "en-orig": _track("auto-en")}
    t = ys.pick_caption_track({}, auto, lang="de", spoken="en")
    assert t["url"] == "auto-de" and t["translated"] is True


def test_live_chat_is_never_mistaken_for_subtitles():
    assert ys.pick_caption_track({"live_chat": _track("chat")}, {}) is None


def test_json3_parsing_drops_empty_events_and_repeats():
    data = {"events": [
        {"tStartMs": 0, "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
        {"tStartMs": 1500, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2000, "segs": [{"utf8": "hello world"}]},
        {"tStartMs": 3000},
        {"tStartMs": 4250, "segs": [{"utf8": "second\nline"}]},
    ]}
    assert ys.parse_json3(data) == [(0.0, "hello world"), (4.25, "second line")]


def test_transcript_is_paragraphed_with_chapter_headings():
    segs = [(0, "intro"), (10, "more intro"), (31, "still intro"), (65, "setup begins"), (70, "setup")]
    chapters = [{"start": 0, "end": 60, "title": "Intro"}, {"start": 60, "end": 120, "title": "Setup"}]
    text = ys.format_transcript(segs, chapters, every=30)
    assert "### Intro (0:00)" in text and "### Setup (1:00)" in text
    assert "[0:00] intro more intro still intro" in text
    assert "[1:05] setup begins setup" in text
    assert text.index("### Setup") < text.index("setup begins")
    assert "[" not in ys.format_transcript(segs, timestamps=False)


def test_most_replayed_skips_the_opening_and_adjacent_bins():
    heat = [{"start": i * 2.0, "end": i * 2.0 + 2, "value": v}
            for i, v in enumerate([1.0, 0.2, 0.9, 0.85, 0.1, 0.1, 0.1, 0.7, 0.1, 0.1])]
    peaks = ys.most_replayed(heat, top=3)
    starts = [p["start"] for p in peaks]
    assert 0.0 not in starts                    # the opening always "peaks"
    assert starts == [4.0, 14.0]                # 0.85 at 6.0 sits next to 0.9 at 4.0


def test_fmt_ts():
    assert ys.fmt_ts(0) == "0:00"
    assert ys.fmt_ts(65.9) == "1:05"
    assert ys.fmt_ts(3725) == "1:02:05"


def test_suggestions_parse_both_answer_shapes():
    assert ys.parse_suggestions(["q", ["a", "b", 3]]) == ["a", "b"]
    jsonp = 'window.google.ac.h(["q",[["q video",0,[512]],["q mode",0,[22,30]]],{"k":1}])'
    assert ys.parse_suggestions(jsonp) == ["q video", "q mode"]
    assert ys.parse_suggestions({"not": "a list"}) == []
    assert ys.parse_suggestions("garbage") == []


# ── Data API helpers ─────────────────────────────────────────────────────────

def test_iso_durations_and_rates(monkeypatch):
    from tools import youtube as Y
    assert Y._iso_duration("PT1H2M3S") == 3723
    assert Y._iso_duration("PT45S") == 45
    assert Y._iso_duration("P1DT1S") == 86401
    assert Y._iso_duration("") is None
    assert Y._per_day(1000, "") is None
    today = time.strftime("%Y-%m-%d", time.gmtime())
    assert Y._per_day(1000, today) == 1000            # published today counts as one day


def test_the_api_key_is_sent_as_a_header_never_in_the_url(monkeypatch):
    """A key in the query string ends up in exception text and logs."""
    from tools import youtube as Y
    seen = {}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, params=None, headers=None):
            seen.update(url=url, params=params, headers=headers)
            return httpx.Response(200, json={"items": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(cfg, "youtube_api_key", "SECRET-KEY", raising=False)
    monkeypatch.setattr(Y.httpx, "AsyncClient", Client)
    asyncio.run(Y._api("videos", {"id": "x"}))
    assert "SECRET-KEY" not in seen["url"] and "SECRET-KEY" not in str(seen["params"])
    assert seen["headers"]["X-Goog-Api-Key"] == "SECRET-KEY"


def test_google_error_bodies_are_explained():
    from tools.youtube import api_error
    r = httpx.Response(403, json={"error": {"message": "quota gone", "errors": [{"reason": "quotaExceeded"}]}})
    e = api_error(r)
    assert e.status == 403 and e.reason == "quotaExceeded" and "quota gone" in str(e)


# ── tools, with the page reads stubbed ───────────────────────────────────────

def test_registration_and_the_one_writer():
    from tools.youtube import register_youtube_tools
    from tools.youtube_studio import register_youtube_studio_tools
    tools = {**_tools(register_youtube_tools), **_tools(register_youtube_studio_tools)}
    assert set(tools) == {
        "youtube_search", "youtube_channel", "youtube_channel_videos", "youtube_video",
        "youtube_watch", "youtube_transcript", "youtube_comments", "youtube_keywords",
        "youtube_trending", "youtube_ask_video", "youtube_track", "youtube_track_report",
        "youtube_analytics", "youtube_reach", "youtube_reach_setup", "search_console_query"}
    writers = {n for n, t in tools.items() if not t.annotations.readOnlyHint}
    assert writers == {"youtube_track", "youtube_reach_setup"}
    from core.agent_permissions import is_outward
    assert not any(is_outward(n, t.annotations.readOnlyHint) for n, t in tools.items())


def _listing(entries, **kw):
    return {"channel": "Chan", "channel_id": "UCGKDBMhTGqvlBZE9cF5d50Q", "handle": "@chan",
            "subscribers": 1100, "description": "", "tags": [], "entries": entries, **kw}


def test_channel_videos_without_a_key_says_the_numbers_are_rounded(monkeypatch):
    from tools import youtube as Y
    monkeypatch.setattr(cfg, "youtube_api_key", "", raising=False)
    monkeypatch.setattr(ys, "channel_listing", lambda ref, tab, n: _listing([
        {"id": "a" * 11, "title": "A | pipe", "views": 11000, "duration": 251},
        {"id": "b" * 11, "title": "B", "views": 1000, "duration": 300},
        {"id": "c" * 11, "title": "C", "views": 3000, "duration": 60}]))
    out = _run(_tools(Y.register_youtube_tools)["youtube_channel_videos"],
               {"channel": "@chan", "sort": "views"})
    assert "rounded above 1,000" in out
    assert "Median views: 3,000" in out
    assert out.index("A / pipe") < out.index("| C |") < out.index("| B |")   # sorted, pipe escaped
    assert "| 3.67 |" in out                                                  # 11000 / 3000


def test_channel_videos_with_a_key_uses_exact_numbers(monkeypatch):
    from tools import youtube as Y
    monkeypatch.setattr(cfg, "youtube_api_key", "k", raising=False)
    monkeypatch.setattr(ys, "channel_listing", lambda ref, tab, n: _listing([
        {"id": "a" * 11, "title": "A", "views": 11000, "duration": None}]))

    async def stats(ids, token=""):
        return {"a" * 11: {"views": 11234, "likes": 500, "comments": 62, "published": "2026-01-01",
                           "duration": 251, "title": "A"}}
    monkeypatch.setattr(Y, "video_stats", stats)
    out = _run(_tools(Y.register_youtube_tools)["youtube_channel_videos"], {"channel": "@chan"})
    assert "Data API (exact)" in out and "11,234" in out and "2026-01-01" in out
    assert "| 5.0 |" in out                        # (500 + 62) / 11234 = 5.0 %


def test_watch_assembles_description_chapters_peaks_and_transcript(monkeypatch):
    from tools import youtube as Y
    video = ys.normalize_video({
        "id": "dQw4w9WgXcQ", "title": "T", "channel": "C", "uploader_id": "@c",
        "channel_follower_count": 10, "view_count": 1234, "like_count": 5, "comment_count": 2,
        "duration": 120, "upload_date": "20260102", "description": "desc here", "tags": ["x", "y"],
        "chapters": [{"start_time": 0, "end_time": 60, "title": "Intro"},
                     {"start_time": 60, "end_time": 120, "title": "Main"}],
        "heatmap": [{"start_time": i * 12, "end_time": i * 12 + 12, "value": v}
                    for i, v in enumerate([1, .1, .2, .9, .1, .1, .1, .1, .1, .1])],
        "language": "en"})
    monkeypatch.setattr(ys, "video_info", lambda vid: video)
    monkeypatch.setattr(ys, "fetch_transcript", lambda v, lang="": {
        "lang": "en", "kind": "auto", "translated": False,
        "segments": [(1, "hello"), (61, "main part")]})
    out = _run(_tools(Y.register_youtube_tools)["youtube_watch"], {"video": "https://youtu.be/dQw4w9WgXcQ"})
    for want in ("# T", "2026-01-02", "1,234", "desc here", "x, y", "- 1:00 Main",
                 "0:36–0:48 · 0.90", "YouTube auto-captions", "### Main (1:00)", "main part"):
        assert want in out, want


def test_a_video_without_captions_still_gets_its_metadata(monkeypatch):
    from tools import youtube as Y
    monkeypatch.setattr(ys, "video_info", lambda vid: ys.normalize_video({"id": vid, "title": "No caps"}))

    def none(v, lang=""):
        raise ys.ScrapeError("this video has no captions")
    monkeypatch.setattr(ys, "fetch_transcript", none)
    out = _run(_tools(Y.register_youtube_tools)["youtube_watch"], {"video": "dQw4w9WgXcQ"})
    assert "# No caps" in out and "Not available: this video has no captions" in out


def test_trending_is_the_only_tool_that_insists_on_a_key(monkeypatch):
    from core.tool_registry import is_tool_environment_ready, looks_like_missing_service_config
    from tools import youtube as Y
    monkeypatch.setattr(cfg, "youtube_api_key", "", raising=False)
    out = _run(_tools(Y.register_youtube_tools)["youtube_trending"], {})
    assert looks_like_missing_service_config(out)
    assert not is_tool_environment_ready("youtube_trending")
    assert is_tool_environment_ready("youtube_watch")
    assert is_tool_environment_ready("youtube_channel_videos")


# ── tracking ─────────────────────────────────────────────────────────────────

def test_tracker_report_is_subtraction_between_real_snapshots(tmp_path):
    cid = "UCGKDBMhTGqvlBZE9cF5d50Q"
    ch, created = store.add_channel(tmp_path, cid, handle="@c", title="Chan")
    assert created and store.add_channel(tmp_path, cid, title="Chan")[1] is False
    day = 86400
    t0 = 1_800_000_000
    store.record_snapshot(tmp_path, cid, subscribers=1000, views=50000, videos=10, source="api",
                          taken_at=t0, uploads=[{"id": "old", "title": "Old", "views": 100,
                                                 "published": "2020-01-01"}])
    store.record_snapshot(tmp_path, cid, subscribers=1100, views=56000, videos=11, source="api",
                          taken_at=t0 + 10 * day,
                          uploads=[{"id": "new", "title": "New", "views": 5000, "published": ""},
                                   {"id": "old", "title": "Old", "views": 400, "published": "2020-01-01"}])
    r = store.report(tmp_path, cid, t0 - day)
    assert r["days"] == pytest.approx(10)
    assert (r["subscribers"], r["views"], r["videos"]) == (100, 6000, 1)
    assert r["mixed_sources"] is False
    assert [u["video_id"] for u in r["new_uploads"]] == ["new"]   # back catalogue is not "new"
    assert [(g["video_id"], g["gained"]) for g in r["top_gainers"]] == [("old", 300)]


def test_an_older_video_reached_by_a_bigger_capture_is_not_a_new_upload(tmp_path):
    """Found live: a snapshot capturing 30 videos after an `add` that captured 10
    reported the 20 older ones as new uploads. Without dates, only a video that
    appears *above* everything already known is new."""
    cid = "UCGKDBMhTGqvlBZE9cF5d50Q"
    store.add_channel(tmp_path, cid, title="Chan")

    def up(*ids):
        return [{"id": i, "title": i, "views": 1, "kind": "video"} for i in ids]
    store.record_snapshot(tmp_path, cid, source="page", taken_at=1000, uploads=up("v3", "v2"))
    store.record_snapshot(tmp_path, cid, source="page", taken_at=2000, uploads=up("v4", "v3", "v2", "v1"))
    new = store.report(tmp_path, cid, 0)["new_uploads"]
    assert [u["video_id"] for u in new] == ["v4"]


def test_the_capture_size_is_remembered_per_channel(tmp_path):
    cid = "UCGKDBMhTGqvlBZE9cF5d50Q"
    assert store.add_channel(tmp_path, cid, title="C")[0]["max_videos"] == 30
    assert store.add_channel(tmp_path, cid, max_videos=50)[0]["max_videos"] == 50
    assert store.add_channel(tmp_path, cid)[0]["max_videos"] == 50       # 0 = keep


def test_one_snapshot_is_not_a_trend(tmp_path):
    cid = "UCGKDBMhTGqvlBZE9cF5d50Q"
    store.add_channel(tmp_path, cid, title="Chan")
    store.record_snapshot(tmp_path, cid, subscribers=5, source="page", taken_at=1_800_000_000)
    r = store.report(tmp_path, cid, 0)
    assert r["start"]["taken_at"] == r["end"]["taken_at"] and "days" not in r


def test_mixing_rounded_and_exact_readings_is_flagged(tmp_path):
    cid = "UCGKDBMhTGqvlBZE9cF5d50Q"
    store.add_channel(tmp_path, cid, title="Chan")
    store.record_snapshot(tmp_path, cid, subscribers=1100, source="page", taken_at=1)
    store.record_snapshot(tmp_path, cid, subscribers=1123, source="api", taken_at=86401)
    assert store.report(tmp_path, cid, 0)["mixed_sources"] is True


def test_find_and_remove_keep_history(tmp_path):
    cid = "UCGKDBMhTGqvlBZE9cF5d50Q"
    store.add_channel(tmp_path, cid, handle="@The_Frizzy1", title="The_Frizzy1")
    store.record_snapshot(tmp_path, cid, subscribers=1, taken_at=1)
    assert store.find_channel(tmp_path, "the_frizzy1")["channel_id"] == cid
    assert store.find_channel(tmp_path, "@THE_FRIZZY1")["channel_id"] == cid
    assert store.remove_channel(tmp_path, cid) is True
    assert store.find_channel(tmp_path, cid) is None
    store.add_channel(tmp_path, cid, title="back")
    assert store.list_channels(tmp_path)[0]["snapshots"] == 1


def test_track_tool_add_then_report(tmp_path, monkeypatch):
    from tools import youtube as Y
    monkeypatch.setattr(Y, "_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "youtube_api_key", "", raising=False)

    def listing(ref, tab, n):
        if tab == "shorts":
            raise ys.ScrapeError("This channel does not have a shorts tab")
        return _listing([{"id": "a" * 11, "title": "A", "views": 900, "duration": 10}])
    monkeypatch.setattr(ys, "channel_listing", listing)
    tools = _tools(Y.register_youtube_tools)
    out = _run(tools["youtube_track"], {"action": "add", "channel": "@chan", "note": "rival"})
    assert "Now tracking **Chan**" in out and "youtube_track" in out
    assert "| Chan | @chan | 1,100 | 1 |" in _run(tools["youtube_track"], {"action": "list"})
    assert "Change needs a second one" in _run(tools["youtube_track_report"], {})


# ── Google login ─────────────────────────────────────────────────────────────

@pytest.fixture
def google_client(monkeypatch):
    monkeypatch.setattr(cfg, "google_oauth_client_id", "cid.apps.googleusercontent.com", raising=False)
    monkeypatch.setattr(cfg, "google_oauth_client_secret", "shh", raising=False)
    monkeypatch.setattr(cfg, "google_oauth_redirect_uri", "", raising=False)
    go._PENDING.clear()
    go._ACCESS.clear()
    yield
    go._PENDING.clear()
    go._ACCESS.clear()


def test_the_consent_url_asks_for_offline_access_with_pkce(google_client):
    from urllib.parse import parse_qs, urlparse
    s = go.start()
    q = {k: v[0] for k, v in parse_qs(urlparse(s["auth_url"]).query).items()}
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) >= 43
    assert q["state"] == s["state"]
    assert q["redirect_uri"].startswith("http://localhost:") and q["redirect_uri"].endswith(go.CALLBACK_PATH)
    for scope in go.SCOPES:
        assert scope in q["scope"]
    assert go._challenge(go._PENDING[s["state"]]["verifier"]) == q["code_challenge"]


def test_without_a_client_the_tools_explain_the_setup(monkeypatch):
    monkeypatch.setattr(cfg, "google_oauth_client_id", "", raising=False)
    from core.tool_registry import looks_like_missing_service_config
    from tools import youtube_studio as ST
    tools = _tools(ST.register_youtube_studio_tools)
    for name, payload in (("youtube_analytics", {}), ("youtube_reach", {}), ("youtube_reach_setup", {}),
                          ("search_console_query", {})):
        out = _run(tools[name], payload)
        assert "GOOGLE_OAUTH_CLIENT_ID" in out and looks_like_missing_service_config(out), name


@pytest.mark.parametrize("text,expect", [
    ("http://localhost:8766/api/v1/google/callback?state=S&code=4/abc&scope=x", ("4/abc", "S", "")),
    ("?code=C1&state=S1", ("C1", "S1", "")),
    ("http://localhost/cb?error=access_denied&state=S", ("", "S", "access_denied")),
    ("4/0AbareCode", ("4/0AbareCode", "", "")),
])
def test_pasted_redirects_are_understood(text, expect):
    assert go.parse_response(text) == expect


def _stub_google(monkeypatch, *, token_status=200, refresh=True):
    calls = []

    async def post(url, data):
        calls.append((url, dict(data)))
        if url == go.TOKEN_URL and data.get("grant_type") == "refresh_token":
            if token_status != 200:
                return token_status, {"error": "invalid_grant"}
            return 200, {"access_token": "fresh", "expires_in": 3600}
        if url == go.TOKEN_URL:
            body = {"access_token": "at-1", "expires_in": 3600, "scope": " ".join(go.SCOPES)}
            if refresh:
                body["refresh_token"] = "rt-secret"
            return 200, body
        return 200, {}

    async def get(url, params, token):
        return 200, {"items": [{"id": "UCGKDBMhTGqvlBZE9cF5d50Q", "snippet": {"title": "The_Frizzy1"}}]}
    monkeypatch.setattr(go, "_post_form", post)
    monkeypatch.setattr(go, "_get_json", get)
    return calls


def test_finishing_stores_the_login_and_status_never_shows_it(google_client, monkeypatch, tmp_path):
    calls = _stub_google(monkeypatch)
    s = go.start()
    st = asyncio.run(go.finish(tmp_path, f"http://localhost/cb?state={s['state']}&code=CODE"))
    exchange = calls[0][1]
    assert exchange["code_verifier"] and exchange["code"] == "CODE"
    assert st["connected"] and st["channel_title"] == "The_Frizzy1" and st["missing_scopes"] == []
    assert "rt-secret" not in str(st)
    assert asyncio.run(go.access_token(tmp_path)) == "at-1"         # cached, no refresh call
    assert s["state"] not in go._PENDING                             # single use


def test_a_state_this_process_did_not_issue_is_refused(google_client, monkeypatch, tmp_path):
    _stub_google(monkeypatch)
    go.start()
    with pytest.raises(ValueError, match="not started here"):
        asyncio.run(go.finish(tmp_path, "http://localhost/cb?state=forged&code=CODE"))


def test_no_refresh_token_is_an_error_not_a_login_that_dies_in_an_hour(google_client, monkeypatch, tmp_path):
    _stub_google(monkeypatch, refresh=False)
    s = go.start()
    with pytest.raises(ValueError, match="no refresh token"):
        asyncio.run(go.finish(tmp_path, f"?state={s['state']}&code=C"))
    assert go.status(tmp_path)["connected"] is False


def test_an_expired_login_says_to_reconnect(google_client, monkeypatch, tmp_path):
    _stub_google(monkeypatch, token_status=400)
    s = go.start()
    asyncio.run(go.finish(tmp_path, f"?state={s['state']}&code=C"))
    go._ACCESS.clear()
    with pytest.raises(go.NotConnected, match="In production"):
        asyncio.run(go.access_token(tmp_path))


def test_disconnect_forgets_the_login(google_client, monkeypatch, tmp_path):
    calls = _stub_google(monkeypatch)
    s = go.start()
    asyncio.run(go.finish(tmp_path, f"?state={s['state']}&code=C"))
    st = asyncio.run(go.disconnect(tmp_path))
    assert st["connected"] is False
    assert any(url == go.REVOKE_URL for url, _ in calls)
    with pytest.raises(go.NotConnected):
        asyncio.run(go.access_token(tmp_path))


def test_the_callback_route_only_finishes_a_started_sign_in(google_client):
    from fastapi.testclient import TestClient
    from ui.api import build_ui_app
    c = TestClient(build_ui_app())
    assert c.get(go.CALLBACK_PATH + "?code=x").status_code == 400                 # no state
    r = c.get(go.CALLBACK_PATH + "?code=x&state=never-issued")
    assert r.status_code == 400 and "not started here" in r.text
    assert c.get("/api/v1/google/status").status_code == 401                      # the rest is authed


# ── analytics & reach formatting ─────────────────────────────────────────────

def test_analytics_search_terms_query_and_table(google_client, monkeypatch, tmp_path):
    from tools import youtube_studio as ST
    monkeypatch.setattr(ST, "_ROOT", tmp_path)
    seen = {}

    async def token(root):
        return "tok"

    async def google(method, url, tok, params=None, json=None):
        seen.update(params)
        return {"columnHeaders": [{"name": "insightTrafficSourceDetail"}, {"name": "views"},
                                  {"name": "estimatedMinutesWatched"}],
                "rows": [["comfyui low vram", 120, 300.0], ["ltx 2.5", 80, 90.0]]}
    monkeypatch.setattr(go, "access_token", token)
    monkeypatch.setattr(ST, "_google", google)
    out = _run(_tools(ST.register_youtube_studio_tools)["youtube_analytics"],
               {"report": "search_terms", "max_results": 50, "start_date": "2026-09-01", "end_date": "2026-09-27"})
    assert seen["filters"] == "insightTrafficSourceType==YT_SEARCH"
    assert seen["maxResults"] == 25                       # YouTube's own cap for this report
    assert seen["ids"] == "channel==MINE" and seen["startDate"] == "2026-09-01"
    assert "| comfyui low vram | 120 | 300 min (5.0 h) |" in out


def test_retention_needs_a_video(google_client):
    from tools import youtube_studio as ST
    out = _run(_tools(ST.register_youtube_studio_tools)["youtube_analytics"], {"report": "retention"})
    assert "pass `video`" in out


def test_reach_reports_dedupe_backfills_and_weight_ctr():
    reps = yr.latest_per_period([
        {"id": "1", "startTime": "d1", "endTime": "d2", "createTime": "a"},
        {"id": "2", "startTime": "d1", "endTime": "d2", "createTime": "b"},     # backfill wins
        {"id": "3", "startTime": "d0", "endTime": "d1", "createTime": "a"}])
    assert [r["id"] for r in reps] == ["3", "2"]
    rows = yr.parse_reach_csv(
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "20260901,UC1,v1,1000,0.05\n20260902,UC1,v1,3000,0.01\n20260901,UC1,v2,500,0.1\nbad,row\n")
    agg = yr.aggregate(rows)
    assert agg["videos"]["v1"] == {"impressions": 4000, "ctr": pytest.approx(0.02)}
    assert agg["total"]["impressions"] == 4500
    assert agg["total"]["ctr"] == pytest.approx((50 + 30 + 50) / 4500)


def test_report_downloads_stay_on_google():
    with pytest.raises(yr.ReportingError):
        asyncio.run(yr.download("t", "https://evil.example/report.csv"))


# ── against YouTube itself ───────────────────────────────────────────────────

@pytest.mark.live
def test_live_transcript_of_a_public_video():
    pytest.importorskip("yt_dlp")
    v = ys.video_info("dQw4w9WgXcQ")
    assert v["title"] and v["views"] and v["heatmap"]
    t = ys.fetch_transcript(v)
    assert len(t["segments"]) > 20


def test_reach_setup_registers_once_and_reuses_the_job(monkeypatch, tmp_path):
    """The report job is created only when none exists — a second run must not make a second job."""
    from tools import youtube_studio as ST
    monkeypatch.setattr(ST, "_ROOT", tmp_path)

    async def tok(_root):
        return "AT"
    monkeypatch.setattr(go, "access_token", tok)
    jobs, posts = [], []

    async def call(method, url, token, *, params=None, json=None, raw=False):
        if method == "GET":
            return {"jobs": list(jobs)}
        posts.append(json)
        jobs.append({"id": "J1", "reportTypeId": yr.REACH_TYPE, "createTime": "2026-10-01T18:00:00Z"})
        return jobs[-1]
    monkeypatch.setattr(yr, "_call", call)
    tool = _tools(ST.register_youtube_studio_tools)["youtube_reach_setup"]
    assert "registered" in _run(tool, {})
    assert "already set up (2026-10-01)" in _run(tool, {})
    assert len(posts) == 1 and posts[0]["reportTypeId"] == yr.REACH_TYPE


def test_dated_search_uses_youtubes_upload_filter(monkeypatch):
    """YouTube dropped sort-by-date (sp=CAI%3D is ignored, ytsearchdate is gone); the
    upload-date filter is what still works, so 'date' maps to 'uploaded this week'."""
    seen = []

    def fake(url, **kw):
        seen.append((url, kw))
        return {"entries": [{"id": "dQw4w9WgXcQ", "title": "t"}]}
    monkeypatch.setattr(ys, "_extract", fake)
    ys.search("comfy ui", 5, "date")
    ys.search("comfy ui", 5, "month")
    ys.search("comfy ui", 5)
    assert seen[0][0] == "https://www.youtube.com/results?search_query=comfy+ui&sp=EgIIAw%3D%3D"
    assert seen[0][1]["playlistend"] == 5
    assert seen[1][0].endswith("sp=EgIIBA%3D%3D")
    assert seen[2][0] == "ytsearch5:comfy ui"


def test_the_browse_source_is_not_called_subscribers():
    from tools.youtube_studio import TRAFFIC_SOURCES
    assert "Browse features" in TRAFFIC_SOURCES["SUBSCRIBER"]
    assert "BROWSE" not in TRAFFIC_SOURCES
