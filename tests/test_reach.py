"""The Agent Reach channels (tools/reach.py) and keyword volume (tools/keywords.py).

Offline: every network seam is stubbed. What is under test is what Plutus does
with the answers and with the arguments a model hands it — which ids it accepts,
which hosts it refuses, what the twitter-cli child is allowed to see, and how a
Keyword Planner answer becomes a table.
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import httpx
import pytest

from config import cfg
from tools import keywords as K
from tools import reach as R


def _tools(*registers):
    from mcp.server.fastmcp import FastMCP
    m = FastMCP("t")
    for reg in registers:
        reg(m)
    return {t.name: t for t in m._tool_manager.list_tools()}


def _run(tool, payload):
    from core.invoke_tool import invoke_mcp_tool_fn
    return str(asyncio.run(invoke_mcp_tool_fn(tool.fn, payload=payload)))


# ── argument parsing ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ref", [
    "1840000000000000001",
    "https://x.com/someone/status/1840000000000000001",
    "https://twitter.com/someone/status/1840000000000000001?s=20",
    "x.com/someone/status/1840000000000000001/photo/1",
    "https://mobile.twitter.com/someone/statuses/1840000000000000001",
])
def test_tweet_ids_are_read_from_every_url_shape(ref):
    assert R.parse_tweet_id(ref) == "1840000000000000001"


@pytest.mark.parametrize("ref", ["", "abc", "https://evil.example/someone/status/1840000000000000001",
                                 "--max=999"])
def test_a_tweet_on_another_host_or_an_option_is_refused(ref):
    with pytest.raises(ValueError):
        R.parse_tweet_id(ref)


def test_handles_are_normalised_and_validated():
    assert R.parse_handle("@the_frizzy1") == "the_frizzy1"
    assert R.parse_handle("https://x.com/the_frizzy1/with_replies") == "the_frizzy1"
    for bad in ("--json", "a b", "x" * 16, "https://evil.example/someone"):
        with pytest.raises(ValueError):
            R.parse_handle(bad)


def test_bilibili_and_v2ex_ids():
    assert R.parse_bvid("https://www.bilibili.com/video/BV197jc6qEDA/?spm=x") == "BV197jc6qEDA"
    assert R.parse_bvid("BV197jc6qEDA") == "BV197jc6qEDA"
    with pytest.raises(ValueError):
        R.parse_bvid("av12345")
    assert R.parse_v2ex_topic("https://www.v2ex.com/t/1246024#reply3") == 1246024
    assert R.parse_v2ex_topic("1246024") == 1246024
    with pytest.raises(ValueError):
        R.parse_v2ex_topic("nope")


# ── feeds ────────────────────────────────────────────────────────────────────

RSS = """<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
  <title>A blog</title>
  <item><title>First &amp; best</title><link>https://blog.example/1</link>
    <pubDate>Thu, 01 Oct 2026 10:00:00 +0000</pubDate><dc:creator>Ann</dc:creator>
    <description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
  <item><title>Second</title><link>https://blog.example/2</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom feed</title>
  <entry><title>Video one</title><link rel="alternate" href="https://yt.example/v1"/>
    <published>2026-10-01T04:00:27+00:00</published><author><name>Chan</name></author>
    <summary>Sum</summary></entry>
</feed>"""

RDF = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
  <channel><title>RDF feed</title></channel>
  <item><title>Old style</title><link>https://rdf.example/a</link></item>
</rdf:RDF>"""


def test_rss2_entries():
    title, items = R.feed_entries(RSS, 10)
    assert title == "A blog"
    assert items[0]["title"] == "First & best"
    assert items[0]["link"] == "https://blog.example/1"
    assert items[0]["author"] == "Ann"
    assert items[0]["summary"] == "Hello world"       # markup decoded and stripped
    assert len(R.feed_entries(RSS, 1)[1]) == 1


def test_atom_and_rdf_entries():
    title, items = R.feed_entries(ATOM, 5)
    assert title == "Atom feed" and items[0]["link"] == "https://yt.example/v1"
    assert items[0]["author"] == "Chan" and items[0]["date"].startswith("2026-10-01")
    title, items = R.feed_entries(RDF, 5)
    assert title == "RDF feed" and items[0]["link"] == "https://rdf.example/a"


def test_junk_is_not_a_feed():
    assert R.feed_entries("", 5) == ("", [])
    assert R.feed_entries("<html><body>hi</body></html>", 5) == ("", [])
    assert R.feed_entries("not xml at all <", 5) == ("", [])


def test_rss_read_refuses_a_lan_address():
    tools = _tools(R.register_reach_tools)
    out = _run(tools["rss_read"], {"url": "http://192.168.1.111:8096/feed"})
    assert out.startswith("Error:") and "private" in out


def test_web_read_refuses_a_lan_address_before_calling_jina(monkeypatch):
    """Jina fetches from its own network, but an internal hostname still must not
    be handed to a third party."""
    def boom(*a, **k):
        raise AssertionError("Jina was called")
    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    out = _run(_tools(R.register_reach_tools)["web_read"], {"url": "http://127.0.0.1:8766/"})
    assert out.startswith("Error:")


def test_rss_redirect_hops_are_screened(monkeypatch):
    """A public feed that redirects to a LAN address must be refused at the hop."""
    def handler(request: httpx.Request):
        if request.url.host == "feed.example":
            return httpx.Response(302, headers={"Location": "http://10.0.0.5/admin"})
        raise AssertionError(f"followed to {request.url}")

    screened = []

    def screen(url):
        screened.append(url)
        return "Refusing to fetch a private/internal address." if "10.0.0.5" in url else None

    import core.ssrf_guard as guard
    monkeypatch.setattr(guard, "screen_url", screen)
    real = httpx.AsyncClient
    monkeypatch.setattr(R.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(PermissionError):
        asyncio.run(R.fetch_screened("https://feed.example/rss"))
    assert screened == ["https://feed.example/rss", "http://10.0.0.5/admin"]


# ── X / twitter-cli ──────────────────────────────────────────────────────────

def test_twitter_is_never_started_without_both_cookies(monkeypatch):
    monkeypatch.setattr(cfg, "twitter_auth_token", "tok")
    monkeypatch.setattr(cfg, "twitter_ct0", "")
    monkeypatch.setattr(R, "twitter_command", lambda: ["twitter"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("started without cookies"))
    with pytest.raises(R.TwitterError, match="not configured"):
        R._run_twitter_sync(["search", "--", "x"])


def test_twitter_child_sees_only_the_sandbox_and_the_cookies(monkeypatch, tmp_path):
    """twitter-cli reads browser cookie stores when it can find them. The child's
    home and app-data dirs all point into Plutus's sandbox, and none of Plutus's
    own secrets ride along."""
    monkeypatch.setattr(cfg, "twitter_auth_token", "AUTH")
    monkeypatch.setattr(cfg, "twitter_ct0", "CT0")
    monkeypatch.setattr(R, "_ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    monkeypatch.setenv("TWITTER_BROWSER", "chrome")
    env, cwd = R.twitter_env()
    assert env["TWITTER_AUTH_TOKEN"] == "AUTH" and env["TWITTER_CT0"] == "CT0"
    sandbox = str(tmp_path / "data" / "twitter-cli" / "home")
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        assert env[k].startswith(sandbox), k
    assert "GITHUB_TOKEN" not in env and "TWITTER_BROWSER" not in env
    assert cwd == sandbox


def _fake_run(stdout: dict | str, code: int = 0, calls: list | None = None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        out = stdout if isinstance(stdout, str) else json.dumps(stdout)
        return subprocess.CompletedProcess(cmd, code, out.encode(), b"")
    return run


TWEET = {"id": "1840000000000000001", "text": "LTX 2.5 on a 3060\nworks",
         "author": {"screenName": "someone", "name": "Some One"},
         "metrics": {"likes": 1200, "retweets": 30, "replies": 4, "views": 56000},
         "createdAtISO": "2026-10-01T10:00:00Z"}


def test_twitter_search_formats_posts_and_ends_options(monkeypatch):
    calls = []
    monkeypatch.setattr(cfg, "twitter_auth_token", "AUTH")
    monkeypatch.setattr(cfg, "twitter_ct0", "CT0")
    monkeypatch.setattr(R, "twitter_command", lambda: ["twitter"])
    monkeypatch.setattr(subprocess, "run", _fake_run({"ok": True, "schema_version": "1",
                                                      "data": [TWEET]}, calls=calls))
    out = _run(_tools(R.register_reach_tools)["twitter_search"], {"query": "--output=/etc/x"})
    # The query is a positional after "--": it can never be parsed as an option.
    assert calls[0][-2:] == ["--", "--output=/etc/x"]
    assert "**@someone**" in out and "1.2k" in out
    assert "https://x.com/someone/status/1840000000000000001" in out


def test_rejected_cookies_say_what_to_do(monkeypatch):
    monkeypatch.setattr(cfg, "twitter_auth_token", "AUTH")
    monkeypatch.setattr(cfg, "twitter_ct0", "CT0")
    monkeypatch.setattr(R, "twitter_command", lambda: ["twitter"])
    monkeypatch.setattr(subprocess, "run", _fake_run(
        {"ok": False, "error": {"code": "auth", "message": "Cookie expired or invalid (HTTP 401)."}}, 1))
    out = _run(_tools(R.register_reach_tools)["twitter_tweet"], {"tweet": "1840000000000000001"})
    assert out.startswith("Error:") and "export fresh" in out


def test_unconfigured_twitter_reads_as_config_not_failure(monkeypatch):
    from core.tool_registry import looks_like_missing_service_config
    monkeypatch.setattr(R, "twitter_command", lambda: [])
    out = _run(_tools(R.register_reach_tools)["twitter_search"], {"query": "x"})
    assert looks_like_missing_service_config(out)


# ── doctor ───────────────────────────────────────────────────────────────────

def test_doctor_lists_every_agent_reach_platform(monkeypatch):
    monkeypatch.setattr(R, "twitter_command", lambda: [])
    rows = {r["platform"]: r for r in R.doctor_rows()}
    for p in ("Web pages", "Web search", "YouTube", "GitHub", "Reddit", "X / Twitter",
              "Bilibili", "V2EX", "RSS / Atom", "LinkedIn", "Search volume"):
        assert p in rows, p
    assert rows["X / Twitter"]["status"] == "setup"
    assert any(r["status"] == "unavailable" and "XiaoHongShu" in r["platform"] for r in rows.values())


def test_doctor_tools_exist():
    """Every tool the doctor names must be a real registered tool."""
    import re
    from ui.runtime import mcp
    names = {t.name for t in mcp._tool_manager.list_tools()}
    for r in R.doctor_rows():
        for tool in re.findall(r"\b[a-z]+_[a-z_]+\b", r["tools"]):
            if not tool.endswith("_"):
                assert tool in names, (r["platform"], tool)


# ── keyword volume ───────────────────────────────────────────────────────────

def test_geo_and_language_ids():
    assert K.geo_id("de") == 2276 and K.geo_id("US") == 2840 and K.geo_id("") is None
    assert K.geo_id("2276") == 2276
    assert K.language_id("de") == 1001 and K.language_id("en") == 1000
    with pytest.raises(ValueError):
        K.geo_id("Narnia")


def test_keywords_are_split_deduped_and_capped():
    assert K.parse_keywords("AI, ai ,ComfyUI\nltx  2.5;;", 50) == ["ai", "comfyui", "ltx 2.5"]
    assert len(K.parse_keywords(",".join(f"k{i}" for i in range(80)), 50)) == 50


def test_google_answer_becomes_a_table():
    results = [{"text": "comfyui", "keywordMetrics": {
        "avgMonthlySearches": "90500", "competition": "LOW", "competitionIndex": "4",
        "lowTopOfPageBidMicros": "120000", "highTopOfPageBidMicros": "1530000",
        "monthlySearchVolumes": [{"month": "AUGUST", "year": "2026", "monthlySearches": "110000"},
                                 {"month": "JULY", "year": "2026", "monthlySearches": "90500"}]}}]
    rows = K.rows_from_google(results, "keywordMetrics")
    assert rows[0]["avg"] == 90500 and rows[0]["bid_high"] == 1.53
    assert rows[0]["monthly"] == [(2026, 7, 90500), (2026, 8, 110000)]
    out = K.render(rows, title="t", source="s", show_monthly=True)
    assert "| comfyui | 90,500 | LOW | 4 | 0.12–1.53 |" in out
    assert "2026-07: 90,500 · 2026-08: 110,000" in out


def test_dataforseo_answer_becomes_rows():
    rows = K.rows_from_dataforseo([{"keyword": "ai", "search_volume": 1830000, "competition": "LOW",
                                    "competition_index": 2, "low_top_of_page_bid": 0.5,
                                    "high_top_of_page_bid": 3.1,
                                    "monthly_searches": [{"year": 2026, "month": 8, "search_volume": 2240000}]}])
    assert rows[0]["avg"] == 1830000 and rows[0]["currency"] == "USD"
    assert rows[0]["monthly"] == [(2026, 8, 2240000)]


def test_keyword_volume_without_a_backend_explains_setup(monkeypatch):
    from core.tool_registry import looks_like_missing_service_config
    for k in ("google_ads_developer_token", "google_ads_customer_id", "dataforseo_login", "dataforseo_password"):
        monkeypatch.setattr(cfg, k, "")
    out = _run(_tools(K.register_keyword_tools)["keyword_volume"], {"keywords": "ai"})
    assert "Basic access" in out and "DataForSEO" in out
    assert looks_like_missing_service_config(out)


def test_google_ads_request_shape(monkeypatch):
    """Historical metrics for given keywords; the manager id rides as login-customer-id."""
    monkeypatch.setattr(cfg, "google_ads_developer_token", "DEVTOKEN")
    monkeypatch.setattr(cfg, "google_ads_customer_id", "123-456-7890")
    monkeypatch.setattr(cfg, "google_ads_login_customer_id", "111-222-3333")
    monkeypatch.setattr(cfg, "google_ads_api_version", "v25")
    from core import google_oauth as go

    async def tok(_root):
        return "AT"
    monkeypatch.setattr(go, "access_token", tok)
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [{"text": "ai", "keywordMetrics": {"avgMonthlySearches": "1000"}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(K.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    out = _run(_tools(K.register_keyword_tools)["keyword_volume"],
               {"keywords": "AI", "country": "DE", "language": "de"})
    assert seen["url"] == "https://googleads.googleapis.com/v25/customers/1234567890:generateKeywordHistoricalMetrics"
    assert seen["headers"]["developer-token"] == "DEVTOKEN"
    assert seen["headers"]["login-customer-id"] == "1112223333"
    assert seen["body"] == {"language": "languageConstants/1001", "keywordPlanNetwork": "GOOGLE_SEARCH",
                            "geoTargetConstants": ["geoTargetConstants/2276"], "keywords": ["ai"]}
    assert "| ai | 1,000 |" in out


def test_explorer_access_error_names_the_fix():
    r = httpx.Response(403, json={"error": {"message": "denied", "details": [{"errors": [
        {"errorCode": {"authorizationError": "DEVELOPER_TOKEN_NOT_APPROVED"},
         "message": "The developer token is only approved for use with test accounts."}]}]}})
    assert "Basic" in K.google_ads_error(r)


def test_ads_scope_is_only_requested_with_a_developer_token(monkeypatch):
    from core import google_oauth as go
    monkeypatch.setattr(cfg, "google_ads_developer_token", "")
    assert go.ADS_SCOPE not in go.requested_scopes()
    monkeypatch.setattr(cfg, "google_ads_developer_token", "DEV")
    assert go.ADS_SCOPE in go.requested_scopes()
