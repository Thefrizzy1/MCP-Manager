"""Firecrawl — reading the pages plain HTTP cannot.

``web_fetch`` fetches and strips tags, which returns almost nothing for a site
that renders in the browser. That failure is quiet: a nav bar and a footer strip
to a couple of hundred characters and look like an answer. So the two things
worth pinning are the request shapes Firecrawl expects, and the point where
web_fetch decides its own answer was too thin to be one.

No key is needed to run these: every network call is stubbed. Firecrawl requires
a key for everything, so the live shapes here come from its documented API rather
than from a verified round trip — the parsing is deliberately tolerant of both
the v1 and v2 response layouts for that reason.
"""
from __future__ import annotations

import asyncio
import json as _json

import httpx
import pytest

import tools.scrape as SC
from core.invoke_tool import invoke_mcp_tool_fn


def _tool(name):
    from mcp.server.fastmcp import FastMCP

    m = FastMCP("t")
    SC.register_scrape_tools(m)
    return {t.name: t.fn for t in m._tool_manager.list_tools()}[name]


def _run(name, payload):
    return str(asyncio.run(invoke_mcp_tool_fn(_tool(name), payload=payload)))


class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.text = text or _json.dumps(self._payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)


def _fake(monkeypatch, routes, *, key="fc-test", base=""):
    """Record calls; answer from `routes` keyed by "METHOD /path-prefix".

    The SSRF screen is stubbed open here — it resolves DNS, so the placeholder
    hostnames these tests use would be refused for not existing rather than for
    being internal. The screen itself is covered by its own tests below, against
    addresses that genuinely are internal.
    """
    seen: list[dict] = []
    monkeypatch.setattr(SC.cfg, "firecrawl_api_key", key, raising=False)
    monkeypatch.setattr(SC.cfg, "firecrawl_api_url", base, raising=False)

    async def allow(_url):
        return ""

    monkeypatch.setattr(SC, "_screen", allow)

    class C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kw):
            path = url.replace(SC.firecrawl_base(), "")
            seen.append({"method": method, "path": path, "json": kw.get("json"),
                         "headers": kw.get("headers") or {}})
            for spec, resp in routes.items():
                verb, _, prefix = spec.partition(" ")
                if method == verb and path.startswith(prefix):
                    return resp() if callable(resp) else resp
            return _Resp({}, 404)

    monkeypatch.setattr(SC.httpx, "AsyncClient", C)
    return seen


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    """A crawl polls on a timer; tests should not actually wait."""
    monkeypatch.setattr(SC, "CRAWL_POLL_SECONDS", 0.01)
    monkeypatch.setattr(SC, "CRAWL_MAX_WAIT", 0.05)


# ── configuration ────────────────────────────────────────────────────────────

def test_without_a_key_every_tool_says_what_to_add(monkeypatch):
    monkeypatch.setattr(SC.cfg, "firecrawl_api_key", "", raising=False)
    monkeypatch.setattr(SC.cfg, "firecrawl_api_url", "", raising=False)
    monkeypatch.setattr(SC.httpx, "AsyncClient",
                        lambda *a, **k: pytest.fail("called the API with no key"))
    for name, payload in (("firecrawl_scrape", {"url": "https://x.com"}),
                          ("firecrawl_map", {"url": "https://x.com"}),
                          ("firecrawl_crawl", {"url": "https://x.com"}),
                          ("firecrawl_search", {"query": "q"})):
        out = _run(name, payload)
        assert "needs a Firecrawl key" in out, name
        assert "FIRECRAWL_API_KEY" in out, name


def test_a_self_hosted_instance_counts_as_configured(monkeypatch):
    """Firecrawl is open source; a self-hosted deployment may need no key at all,
    so requiring one would lock those users out of their own server."""
    monkeypatch.setattr(SC.cfg, "firecrawl_api_key", "", raising=False)
    monkeypatch.setattr(SC.cfg, "firecrawl_api_url", "http://nas:3002", raising=False)
    assert SC.firecrawl_configured() is True
    assert SC.firecrawl_base() == "http://nas:3002"


def test_the_key_is_sent_as_a_bearer(monkeypatch):
    seen = _fake(monkeypatch, {"POST /v2/scrape": _Resp(
        {"data": {"markdown": "hi", "metadata": {"title": "T"}}})})
    _run("firecrawl_scrape", {"url": "https://example.com"})
    assert seen[0]["headers"]["Authorization"] == "Bearer fc-test"


# ── the SSRF screen ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,payload", [
    ("firecrawl_scrape", {"url": "http://169.254.169.254/latest/meta-data/"}),
    ("firecrawl_map", {"url": "http://192.168.1.1/"}),
    ("firecrawl_crawl", {"url": "http://127.0.0.1:8080/"}),
])
def test_an_inward_url_is_refused_before_any_request(monkeypatch, name, payload):
    """Firecrawl normally fetches from its own infrastructure — but a self-hosted
    instance sits inside the LAN, where a model-supplied URL would otherwise be a
    way to reach the metadata service through a service we control."""
    monkeypatch.setattr(SC.cfg, "firecrawl_api_key", "fc-test", raising=False)
    monkeypatch.setattr(SC.httpx, "AsyncClient",
                        lambda *a, **k: pytest.fail("request made despite the screen"))
    out = _run(name, payload)
    assert "private/internal" in out or "Error" in out


# ── scrape ───────────────────────────────────────────────────────────────────

def test_a_page_comes_back_as_markdown(monkeypatch):
    seen = _fake(monkeypatch, {"POST /v2/scrape": _Resp({"data": {
        "markdown": "# Heading\n\nBody text.",
        "metadata": {"title": "Docs", "sourceURL": "https://d.example/x"}}})})
    out = _run("firecrawl_scrape", {"url": "https://d.example/x"})

    assert seen[0]["json"] == {"url": "https://d.example/x",
                               "formats": ["markdown"], "onlyMainContent": True}
    assert "## Docs" in out and "Body text." in out


def test_a_v1_shaped_response_is_still_read(monkeypatch):
    """Self-hosted deployments lag the hosted API, so both layouts must parse."""
    _fake(monkeypatch, {"POST /v2/scrape": _Resp(
        {"markdown": "flat shape", "metadata": {"title": "T"}})})
    assert "flat shape" in _run("firecrawl_scrape", {"url": "https://x.com"})


def test_an_empty_page_says_so_rather_than_returning_nothing(monkeypatch):
    _fake(monkeypatch, {"POST /v2/scrape": _Resp({"data": {"markdown": ""}})})
    out = _run("firecrawl_scrape", {"url": "https://x.com/img.png"})
    assert "no readable content" in out


def test_long_pages_are_clipped_with_a_count(monkeypatch):
    _fake(monkeypatch, {"POST /v2/scrape": _Resp(
        {"data": {"markdown": "x" * 5000, "metadata": {}}})})
    out = _run("firecrawl_scrape", {"url": "https://x.com", "max_chars": 1000})
    assert "more characters" in out and len(out) < 2000


# ── map, crawl, search ───────────────────────────────────────────────────────

def test_mapping_lists_urls(monkeypatch):
    _fake(monkeypatch, {"POST /v2/map": _Resp({"links": [
        {"url": "https://d.example/a", "title": "A"},
        {"url": "https://d.example/b", "title": ""}]})})
    out = _run("firecrawl_map", {"url": "https://d.example", "limit": 10})
    assert "https://d.example/a" in out and "A" in out
    assert "2 URLs" in out


def test_mapping_accepts_a_plain_list_of_strings(monkeypatch):
    _fake(monkeypatch, {"POST /v2/map": _Resp({"links": ["https://d.example/a"]})})
    assert "https://d.example/a" in _run("firecrawl_map", {"url": "https://d.example"})


def test_a_crawl_polls_and_renders_each_page(monkeypatch):
    _fake(monkeypatch, {
        "POST /v2/crawl": _Resp({"id": "job-1"}),
        "GET /v2/crawl/job-1": _Resp({"status": "completed", "data": [
            {"markdown": "page one", "metadata": {"title": "One",
                                                  "sourceURL": "https://d/1"}},
            {"markdown": "page two", "metadata": {"title": "Two"}}]}),
    })
    out = _run("firecrawl_crawl", {"url": "https://d", "limit": 5})
    assert "Crawled 2 pages" in out
    assert "### One" in out and "page two" in out


def test_an_unfinished_crawl_returns_what_it_has(monkeypatch):
    """A crawl can outlast any reasonable tool call; partial beats a timeout."""
    _fake(monkeypatch, {
        "POST /v2/crawl": _Resp({"id": "job-2"}),
        "GET /v2/crawl/job-2": _Resp({"status": "scraping", "data": [
            {"markdown": "first", "metadata": {"title": "First"}}]}),
    })
    out = _run("firecrawl_crawl", {"url": "https://d", "limit": 50})
    assert "First" in out and "partial result" in out and "job-2" in out


def test_a_synchronous_crawl_response_is_handled(monkeypatch):
    _fake(monkeypatch, {"POST /v2/crawl": _Resp({"data": [
        {"markdown": "immediate", "metadata": {"title": "Now"}}]})})
    assert "immediate" in _run("firecrawl_crawl", {"url": "https://d"})


def test_search_returns_results_already_read(monkeypatch):
    seen = _fake(monkeypatch, {"POST /v2/search": _Resp({"data": {"web": [
        {"title": "Result", "url": "https://r/1", "description": "snippet",
         "markdown": "the full page"}]}})})
    out = _run("firecrawl_search", {"query": "homelab backups", "limit": 3})

    assert seen[0]["json"]["query"] == "homelab backups"
    assert seen[0]["json"]["scrapeOptions"]["formats"] == ["markdown"]
    assert "### Result" in out and "snippet" in out and "the full page" in out


def test_search_accepts_the_flat_v1_shape(monkeypatch):
    _fake(monkeypatch, {"POST /v2/search": _Resp(
        {"data": [{"title": "R", "url": "https://r", "description": "d"}]})})
    assert "### R" in _run("firecrawl_search", {"query": "q"})


# ── failures say the useful thing ────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    (401, "rejected the key"),
    (402, "credits exhausted"),
    (429, "rate limit"),
    (504, "timed out"),
])
def test_errors_are_translated(monkeypatch, status, expected):
    _fake(monkeypatch, {"POST /v2/scrape": _Resp({}, status)})
    out = _run("firecrawl_scrape", {"url": "https://x.com"})
    assert expected in out


# ── web_fetch's upgrade path ─────────────────────────────────────────────────

def test_web_fetch_falls_back_when_a_page_is_an_empty_shell(monkeypatch):
    """The quiet failure this exists for: a single-page app leaves a nav bar and
    a footer in the HTML, which strips to something that looks like an answer."""
    import tools.utilities as U

    monkeypatch.setattr(SC.cfg, "firecrawl_api_key", "fc-test", raising=False)
    monkeypatch.setattr(SC.cfg, "firecrawl_api_url", "", raising=False)

    async def fake_call(method, path, payload=None, *, timeout=120.0):
        assert path == "/v2/scrape"
        return {"data": {"markdown": "The real rendered content.",
                         "metadata": {"title": "App"}}}

    monkeypatch.setattr(SC, "_call", fake_call)
    out = asyncio.run(U._render_with_firecrawl("https://spa.example", 5000))
    assert "The real rendered content." in out
    assert "Rendered with Firecrawl" in out, "the upgrade has to be visible"


def test_the_fallback_is_silent_when_firecrawl_is_not_configured(monkeypatch):
    import tools.utilities as U

    monkeypatch.setattr(SC.cfg, "firecrawl_api_key", "", raising=False)
    monkeypatch.setattr(SC.cfg, "firecrawl_api_url", "", raising=False)
    assert asyncio.run(U._render_with_firecrawl("https://x.com", 100)) == ""


def test_a_failing_fallback_leaves_the_original_answer_alone(monkeypatch):
    """A thin answer is bad; turning it into no answer is worse."""
    import tools.utilities as U

    monkeypatch.setattr(SC.cfg, "firecrawl_api_key", "fc-test", raising=False)

    async def boom(*a, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(SC, "_call", boom)
    assert asyncio.run(U._render_with_firecrawl("https://x.com", 100)) == ""


def test_the_thin_page_threshold_is_small_enough_to_be_rare():
    """Set too high, every short-but-real page would burn Firecrawl credits."""
    import tools.utilities as U

    assert 100 <= U._THIN_PAGE_CHARS <= 500
