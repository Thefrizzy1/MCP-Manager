"""Social/community reading tools.

Network calls are not made here — these cover the parts that break silently: Atom
parsing (Reddit is read through feeds because its JSON API is blocked), host
normalisation, and the SSRF screen on federated `instance` arguments, which are
the one place a model can choose the host we connect to.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from tools import social as S

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <author><name>/u/someone</name></author>
    <link href="https://www.reddit.com/r/selfhosted/comments/abc/title/" />
    <updated>2026-07-30T09:00:00+00:00</updated>
    <title>OneDrive alternative that handles 400k files</title>
    <content type="html">&lt;p&gt;Body &amp;amp; more&lt;/p&gt;</content>
  </entry>
  <entry>
    <author><name>/u/other</name></author>
    <link href="https://www.reddit.com/r/selfhosted/comments/def/second/" />
    <updated>2026-07-29T08:00:00+00:00</updated>
    <title>Second post</title>
    <content type="html">&lt;p&gt;Another&lt;/p&gt;</content>
  </entry>
</feed>"""


# ── Atom parsing ─────────────────────────────────────────────────────────────

def test_atom_entries_pulls_the_fields_we_render():
    entries = S.atom_entries(ATOM, 10)
    assert len(entries) == 2
    first = entries[0]
    assert first["title"] == "OneDrive alternative that handles 400k files"
    assert first["author"] == "/u/someone"
    assert first["updated"] == "2026-07-30"
    assert first["link"].endswith("/comments/abc/title/")
    # HTML entities decoded and tags stripped, or the model sees markup.
    assert first["summary"] == "Body & more"


def test_atom_entries_respects_the_limit():
    assert len(S.atom_entries(ATOM, 1)) == 1


def test_atom_entries_survives_junk():
    assert S.atom_entries("", 5) == []
    assert S.atom_entries("<html>not a feed</html>", 5) == []


def test_atom_entries_tolerates_a_missing_field():
    feed = "<feed><entry><title>Only a title</title></entry></feed>"
    e = S.atom_entries(feed, 5)[0]
    assert e["title"] == "Only a title"
    assert e["author"] == "" and e["link"] == ""


# ── federated hosts ──────────────────────────────────────────────────────────

def test_host_is_normalised():
    assert S._host_ok("https://lemmy.world/") == "lemmy.world"
    assert S._host_ok("  MASTODON.social ") == "mastodon.social"


def test_a_host_with_a_path_or_space_is_rejected():
    """It goes straight into a URL, so anything path-shaped must not pass."""
    assert S._host_ok("evil.com/../../admin") == ""
    assert S._host_ok("evil com") == ""
    assert S._host_ok("") == ""


# ── the SSRF screen on caller-chosen hosts ───────────────────────────────────

def _tool(name):
    from mcp.server.fastmcp import FastMCP
    m = FastMCP("t")
    S.register_social_tools(m)
    return {t.name: t.fn for t in m._tool_manager.list_tools()}[name]


def test_a_lemmy_instance_pointing_inward_is_refused(monkeypatch):
    """`instance` is model-supplied, so it is an SSRF vector: an agent reading an
    untrusted page can be told to aim it at 169.254.169.254 or a LAN box."""
    from core.invoke_tool import invoke_mcp_tool_fn

    called = {"http": False}

    def no_http(*a, **k):
        called["http"] = True
        raise AssertionError("a request was made despite the screen")

    monkeypatch.setattr(httpx, "AsyncClient", no_http)
    out = str(asyncio.run(invoke_mcp_tool_fn(
        _tool("lemmy_posts"), payload={"instance": "169.254.169.254", "limit": 1})))

    assert "private/internal" in out
    assert called["http"] is False


def test_a_mastodon_instance_pointing_inward_is_refused(monkeypatch):
    from core.invoke_tool import invoke_mcp_tool_fn

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: pytest.fail("request made despite the screen"))
    out = str(asyncio.run(invoke_mcp_tool_fn(
        _tool("mastodon_timeline"), payload={"instance": "127.0.0.1", "limit": 1})))
    assert "private/internal" in out


# ── rendering ────────────────────────────────────────────────────────────────

def test_counts_are_humanised():
    assert S._fmt_count(999) == "999"
    assert S._fmt_count(1500) == "1.5k"
    assert S._fmt_count(2_000_000) == "2M"
    assert S._fmt_count(None) == "—"


def test_html_is_stripped_and_truncated():
    assert S._strip_html("<p>hello <b>there</b></p>") == "hello there"
    assert len(S._strip_html("x" * 500, 100)) == 100


# ── every tool is registered and read-only ───────────────────────────────────

def test_all_social_tools_register_and_none_can_write():
    from mcp.server.fastmcp import FastMCP

    m = FastMCP("t")
    S.register_social_tools(m)
    tools = {t.name: t for t in m._tool_manager.list_tools()}

    assert set(tools) == {
        "reddit_subreddit_posts", "reddit_search", "reddit_post_comments",
        # Only reachable with a Reddit login — the account's own view.
        "reddit_me", "reddit_my_subreddits", "reddit_home_feed", "reddit_my_posts",
        "hackernews_stories", "hackernews_search", "lemmy_posts",
        "mastodon_timeline", "bluesky_search", "stackexchange_search",
    }
    # Nothing here posts, votes or follows — the annotation has to say so, since
    # the agent's blast-radius rules read it. Authenticating widens what can be
    # *read*, never what can be changed.
    for name, t in tools.items():
        assert t.annotations and t.annotations.readOnlyHint, name


# ── Reddit as the logged-in user ─────────────────────────────────────────────
#
# Anonymously Reddit gives titles and links and nothing else: its JSON API is 403
# for everyone, and the open Atom feeds carry no score or comment count. A script
# app turns the same tools into the account's own view — so what these cover is
# the switch itself: that configuring a login actually moves requests, and that a
# broken login degrades instead of failing.

@pytest.fixture(autouse=True)
def _no_reddit_token():
    S.forget_reddit_token()
    yield
    S.forget_reddit_token()


def _configure(monkeypatch, **over):
    from config import cfg
    values = {"reddit_client_id": "cid", "reddit_client_secret": "secret",
              "reddit_username": "friso", "reddit_password": "pw", **over}
    for k, v in values.items():
        monkeypatch.setattr(cfg, k, v, raising=False)


def _unconfigure(monkeypatch):
    from config import cfg
    for k in ("reddit_client_id", "reddit_client_secret",
              "reddit_username", "reddit_password"):
        monkeypatch.setattr(cfg, k, "", raising=False)


class _Resp:
    def __init__(self, payload, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)


def _fake_reddit(monkeypatch, *, token="tok-1", routes=None, token_status=200):
    """Stand in for reddit.com (the token) and oauth.reddit.com (the data)."""
    seen: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **kw):
            self.headers = kw.get("headers") or {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            seen.append({"method": "POST", "url": url, "data": kw.get("data"),
                         "auth": kw.get("auth")})
            if token_status >= 400:
                return _Resp({"error": "invalid_grant"}, token_status)
            return _Resp({"access_token": token, "expires_in": 3600})

        async def get(self, url, **kw):
            seen.append({"method": "GET", "url": url, "headers": self.headers,
                         "params": kw.get("params")})
            for fragment, payload in (routes or {}).items():
                if fragment in url:
                    return _Resp(payload)
            return _Resp({"data": {"children": []}})

    monkeypatch.setattr(S.httpx, "AsyncClient", FakeClient)
    return seen


LISTING = {"data": {"children": [
    {"data": {"title": "Post &amp; title", "author": "someone",
              "permalink": "/r/x/comments/1/", "score": 4321, "num_comments": 87,
              "subreddit": "selfhosted", "created_utc": 1785000000,
              "selftext": "<p>Body</p>"}},
]}}


def test_without_a_login_reddit_stays_anonymous(monkeypatch):
    _unconfigure(monkeypatch)
    assert S.reddit_configured() is False
    assert asyncio.run(S.reddit_token()) == ""


def test_partial_credentials_are_not_a_login(monkeypatch):
    """Three fields of four is a misconfiguration, not a login — treating it as
    one would fire half-formed auth requests on every call."""
    _configure(monkeypatch, reddit_password="")
    assert S.reddit_configured() is False


def test_a_token_is_fetched_once_and_reused(monkeypatch):
    _configure(monkeypatch)
    seen = _fake_reddit(monkeypatch)

    assert asyncio.run(S.reddit_token()) == "tok-1"
    assert asyncio.run(S.reddit_token()) == "tok-1"
    posts = [c for c in seen if c["method"] == "POST"]
    assert len(posts) == 1, "the token must be cached, not refetched per call"
    assert posts[0]["auth"] == ("cid", "secret")
    assert posts[0]["data"]["grant_type"] == "password"


def test_a_broken_login_degrades_to_anonymous_reading(monkeypatch):
    """A wrong password must not take the tools down with it — the public feeds
    still work, so the answer gets worse rather than disappearing."""
    _configure(monkeypatch)
    _fake_reddit(monkeypatch, token_status=401)
    assert asyncio.run(S.reddit_token()) == ""


def test_a_login_moves_requests_to_the_oauth_host(monkeypatch):
    """The whole point: same tool, different endpoint, richer data."""
    from core.invoke_tool import invoke_mcp_tool_fn

    _configure(monkeypatch)
    seen = _fake_reddit(monkeypatch, routes={"oauth.reddit.com": LISTING})

    out = str(asyncio.run(invoke_mcp_tool_fn(
        _tool("reddit_subreddit_posts"),
        payload={"subreddit": "selfhosted", "sort": "hot", "limit": 5})))

    gets = [c for c in seen if c["method"] == "GET"]
    assert gets and gets[0]["url"].startswith("https://oauth.reddit.com/")
    assert gets[0]["headers"]["Authorization"] == "bearer tok-1"
    # Score and comment count are exactly what the anonymous feeds cannot give.
    assert "4.3k" in out and "87" in out
    assert "r/selfhosted" in out
    assert "Post & title" in out, "entities decoded"
    assert "Reading Reddit anonymously" not in out


def test_without_a_login_the_same_tool_falls_back_to_the_feed(monkeypatch):
    """Same tool, no login: the public Atom feed, and the result says so rather
    than looking like a thin version of the authenticated answer."""
    from core.invoke_tool import invoke_mcp_tool_fn

    _unconfigure(monkeypatch)
    seen = _fake_reddit(monkeypatch)
    monkeypatch.setattr(S, "atom_entries", lambda xml, limit: [
        {"title": "T", "author": "/u/a", "updated": "2026-07-30",
         "link": "https://reddit.com/x", "summary": ""}])

    out = str(asyncio.run(invoke_mcp_tool_fn(
        _tool("reddit_subreddit_posts"), payload={"subreddit": "x", "limit": 2})))

    gets = [c for c in seen if c["method"] == "GET"]
    assert gets and gets[0]["url"].startswith("https://www.reddit.com/")
    assert gets[0]["url"].endswith(".rss")
    assert not any("oauth.reddit.com" in c["url"] for c in seen)
    assert "Reading Reddit anonymously" in out


def test_the_account_view_needs_a_login(monkeypatch):
    """These have no anonymous equivalent, so they say what to do rather than
    returning something misleadingly empty."""
    from core.invoke_tool import invoke_mcp_tool_fn

    _unconfigure(monkeypatch)
    for name, payload in (("reddit_me", {}),
                          ("reddit_my_subreddits", {"limit": 5}),
                          ("reddit_home_feed", {"limit": 5}),
                          ("reddit_my_posts", {"kind": "saved", "limit": 5})):
        out = str(asyncio.run(invoke_mcp_tool_fn(_tool(name), payload=payload)))
        assert "needs a Reddit login" in out, name
        assert "prefs/apps" in out, name


def test_the_account_identity_is_rendered(monkeypatch):
    from core.invoke_tool import invoke_mcp_tool_fn

    _configure(monkeypatch)
    _fake_reddit(monkeypatch, routes={"/api/v1/me": {
        "name": "friso", "link_karma": 1200, "comment_karma": 3400,
        "created_utc": 1500000000, "is_gold": False, "is_mod": True}})

    out = str(asyncio.run(invoke_mcp_tool_fn(_tool("reddit_me"), payload={})))
    assert "u/friso" in out and "1.2k" in out and "3.4k" in out
    assert "Mod: yes" in out


def test_subscriptions_come_from_the_account(monkeypatch):
    from core.invoke_tool import invoke_mcp_tool_fn

    _configure(monkeypatch)
    seen = _fake_reddit(monkeypatch, routes={"subreddits/mine": {"data": {"children": [
        {"data": {"display_name": "selfhosted", "subscribers": 512000,
                  "public_description": "Hosting your own stuff"}}]}}})

    out = str(asyncio.run(invoke_mcp_tool_fn(
        _tool("reddit_my_subreddits"), payload={"limit": 10})))
    assert "r/selfhosted" in out and "512k members" in out
    assert any("/subreddits/mine/subscriber" in c["url"]
               for c in seen if c["method"] == "GET")


def test_an_expired_token_is_refreshed_once(monkeypatch):
    """Reddit tokens expire; a 401 mid-run should be invisible, not an error the
    user has to interpret."""
    from core.invoke_tool import invoke_mcp_tool_fn

    _configure(monkeypatch)
    calls = {"get": 0, "post": 0}

    class C:
        def __init__(self, *a, **kw):
            self.headers = kw.get("headers") or {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls["post"] += 1
            return _Resp({"access_token": "tok-%d" % calls["post"], "expires_in": 3600})

        async def get(self, url, **kw):
            calls["get"] += 1
            if calls["get"] == 1:
                return _Resp({}, 401)          # the cached token just expired
            return _Resp(LISTING)

    monkeypatch.setattr(S.httpx, "AsyncClient", C)
    out = str(asyncio.run(invoke_mcp_tool_fn(
        _tool("reddit_home_feed"), payload={"limit": 5})))

    assert calls["post"] == 2, "a 401 should buy exactly one fresh token"
    assert "Your Reddit front page" in out
