"""
tools/social.py — social + community reading, plus posting to Reddit.

Read-only with two exceptions: `reddit_submit` and `reddit_comment` publish under
one of your own Reddit logins. They are annotated not-read-only and named so that
`core.agent_permissions.is_outward` catches them, which puts them behind the
agent's *publish* switch rather than merely its write switch — an agent allowed to
write to the research library still cannot post in your name. They are safety
level 2 as well, so no smoke test ever fires one.

What each network actually allows, verified against the live endpoints rather than
assumed:

- **Reddit** blocks unauthenticated JSON outright. `www`, `api.`, `oauth.` and
  `old.` all answer "403 Blocked" whatever User-Agent you send, and the open Atom
  feeds carry no score or comment count. So Reddit works two ways here:

  *Anonymous* reads the Atom feeds — titles, authors, links, nothing else.

  *Authenticated* (a script app from https://www.reddit.com/prefs/apps) reads
  `oauth.reddit.com` **as the user**: the full JSON with scores and comment
  counts, a far higher rate limit, and the account's own view — subscriptions,
  front page, saved and upvoted posts. A research agent working from what you
  actually follow beats one guessing at subreddit names.

  Every shared tool takes the better path automatically (`_reddit_entries`), so
  configuring the login upgrades tools that already existed rather than adding a
  parallel set. Nothing requires it; everything is better with it.
- **Bluesky** serves `searchPosts` from `api.bsky.app`. The obvious-looking
  `public.api.bsky.app` returns 403 for that endpoint.
- **Hacker News**, **Lemmy** and **Stack Exchange** answer plain unauthenticated
  JSON.
- **Mastodon** depends on the instance. mastodon.social — the obvious default —
  now sets DISALLOW_UNAUTHENTICATED_API_ACCESS and answers 422 to an anonymous
  timeline read, as does infosec.exchange; mstdn.social, fosstodon.org and mas.to
  still serve it. Hence the default instance here is *not* the biggest one.

Instagram, TikTok, X/Twitter, Facebook, LinkedIn, Threads and Pinterest are
deliberately absent: every one of them now requires a registered OAuth app (and in
X's case a paid tier) even to read public posts, so a card for them would be one
that can never work from credentials a user can simply paste.

`instance` arguments are attacker-reachable — a model can be talked into aiming one
at 169.254.169.254 or a LAN box — so every user-supplied host is screened by
core.ssrf_guard before a request goes out, the same guard web_fetch uses.
"""
import asyncio
import html as _html
import re
from pathlib import Path
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from client import TIMEOUT, _handle_error

UA = "PlutusMCP/1.0 (homelab MCP server)"

# Lemmy and Mastodon are federated: the host is a parameter, not a constant.
DEFAULT_LEMMY = "lemmy.world"
# NOT mastodon.social. It — and a growing number of large instances — now set
# DISALLOW_UNAUTHENTICATED_API_ACCESS, so the public timeline answers 422
# {"error":"This method requires an authenticated user"} to everyone. Verified
# live: mastodon.social and infosec.exchange 422; mstdn.social, fosstodon.org and
# mas.to still serve it. A default that cannot answer is not a default.
DEFAULT_MASTODON = "mstdn.social"

NL = "\n"

# Reddit rate-limits unauthenticated reads aggressively: a few requests in quick
# succession earn a 429 across every endpoint, including ones that answered
# seconds earlier. One request at a time, spaced.
_REDDIT_MIN_INTERVAL = 2.0
_REDDIT_GATE = asyncio.Lock()
_reddit_last = [0.0]


def _fmt_count(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "—"
    for unit, size in (("M", 1_000_000), ("k", 1_000)):
        if abs(n) >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "")
    return str(n)


def _strip_html(s: str, limit: int = 240) -> str:
    """Readable text out of a feed field.

    Unescape *before* stripping tags, not after. Atom carries markup escaped —
    ``&lt;p&gt;Body&lt;/p&gt;`` — so stripping first finds no tags to remove and
    the unescape then hands the model raw ``<p>`` markup. A second unescape
    catches entities that were doubly encoded inside that markup.
    """
    text = _html.unescape(s or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _host_ok(host: str) -> str:
    """Normalise a federated instance host, or return '' if it looks unusable."""
    h = (host or "").strip().lower()
    h = re.sub(r"^https?://", "", h).strip("/")
    if not h or "/" in h or " " in h:
        return ""
    return h


async def _screen(url: str) -> Optional[str]:
    """SSRF check for a host the caller chose. Returns a reason to refuse, or None."""
    from core.ssrf_guard import screen_url
    return await asyncio.to_thread(screen_url, url)


async def _get_json(url: str, params: dict | None = None, *, screen: bool = False):
    if screen:
        blocked = await _screen(url)
        if blocked:
            raise PermissionError(blocked)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                 headers={"User-Agent": UA, "Accept": "application/json"}) as c:
        r = await c.get(url, params=params or {})
        r.raise_for_status()
        return r.json()


# ── Reddit authentication ────────────────────────────────────────────────────
#
# Optional, and worth it. Unauthenticated, Reddit blocks its JSON API outright
# (403 on www/api/oauth/old, whatever User-Agent you send) and rate-limits the
# Atom feeds hard, so an agent gets titles and links and nothing else — no score,
# no comment count, and no way to see the account's own subscriptions or saves.
#
# With a "script" app (https://www.reddit.com/prefs/apps) the same tools go to
# oauth.reddit.com *as the user*: the full JSON, a far higher rate limit, and the
# private endpoints. Nothing here requires it — every tool falls back to the feeds
# — but everything is better with it.

# One cached token per account, keyed by account id. A single shared slot would
# hand the second account the first account's token — every "as me" call would
# quietly answer as the wrong person.
_REDDIT_TOKENS: dict[str, dict] = {}
_REDDIT_AUTH_GATE = asyncio.Lock()
_TOKEN_EARLY_REFRESH = 60.0        # renew a minute early rather than mid-request

_SOCIAL_ROOT = Path(__file__).resolve().parents[1]


def reddit_accounts() -> list[dict]:
    from core import reddit_accounts as ra
    return ra.list_accounts(_SOCIAL_ROOT)


def reddit_configured() -> bool:
    """True when at least one account is fully configured."""
    return bool(reddit_accounts())


def forget_reddit_token(account_id: str = "") -> None:
    if account_id:
        _REDDIT_TOKENS.pop(account_id, None)
    else:
        _REDDIT_TOKENS.clear()


async def reddit_token(account: str = "") -> str:
    """A cached OAuth access token for one account, or "" when none applies.

    Returning "" rather than raising is deliberate: the caller falls back to the
    public feeds, so a missing or broken login degrades the output instead of
    failing the tool. An *unknown* account is the exception — see reddit_auth_error.
    """
    from core import reddit_accounts as ra

    acct = ra.resolve(_SOCIAL_ROOT, account)
    if not acct:
        return ""
    aid = acct["id"]

    now = asyncio.get_running_loop().time()
    async with _REDDIT_AUTH_GATE:
        cached = _REDDIT_TOKENS.get(aid)
        if cached and cached["value"] and cached["expires"] - _TOKEN_EARLY_REFRESH > now:
            return cached["value"]
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as c:
                r = await c.post(
                    "https://www.reddit.com/api/v1/access_token",
                    auth=(acct["client_id"], acct["client_secret"]),
                    data={"grant_type": "password",
                          "username": acct["username"],
                          "password": acct["password"]},
                )
            r.raise_for_status()
            body = r.json()
        except Exception:
            forget_reddit_token(aid)
            return ""
        token = str(body.get("access_token") or "")
        if not token:
            forget_reddit_token(aid)
            return ""
        _REDDIT_TOKENS[aid] = {"value": token,
                               "expires": now + float(body.get("expires_in") or 3600)}
        return token


def reddit_auth_error(account: str) -> str:
    """Why a named account could not be used, or "" when it is fine.

    A typo'd account name must not silently fall back to the public feeds and a
    different identity — that is exactly the "it said it worked" failure the
    write path is supposed to make impossible.
    """
    from core import reddit_accounts as ra

    if not (account or "").strip():
        return ""
    if ra.resolve(_SOCIAL_ROOT, account):
        return ""
    known = ", ".join(a["label"] for a in ra.public_accounts(_SOCIAL_ROOT)) or "none configured"
    return f"No Reddit account matching '{account}'. Known accounts: {known}."


_FULLNAME_RE = re.compile(r"^t[1-6]_[a-z0-9]+$", re.I)
_PERMALINK_RE = re.compile(r"/comments/([a-z0-9]+)(?:/[^/]*/([a-z0-9]+))?", re.I)


def reddit_thing_id(value: str) -> str:
    """Normalise whatever the caller had to hand into a Reddit fullname.

    A model asked to "reply to this post" reaches for the thing it was shown,
    which is a permalink — not the ``t3_…`` fullname the API wants. Accepting
    only fullnames means the tool fails on the most natural input; guessing
    silently means replying to the wrong thing. So: fullnames pass through, a
    permalink is parsed (the comment id wins over the post id when the URL has
    both, because that URL points at the comment), and a bare id is assumed to be
    a post. Anything else returns "" and the caller refuses.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if _FULLNAME_RE.match(v):
        return v.lower()
    m = _PERMALINK_RE.search(v)
    if m:
        return f"t1_{m.group(2).lower()}" if m.group(2) else f"t3_{m.group(1).lower()}"
    if re.fullmatch(r"[a-z0-9]{4,12}", v, re.I):
        return f"t3_{v.lower()}"
    return ""


def reddit_api_error(body: object) -> str:
    """The human-readable error Reddit hid inside a 200 response, or "".

    Reddit answers a rejected submission with HTTP 200 and the reason in
    ``json.errors`` — rate limits, subreddit bans, missing flair, captcha. Treat
    that as success and the agent reports "posted" for something that does not
    exist, which is the one failure mode a publishing tool must not have.
    """
    errors = ((body or {}).get("json") or {}).get("errors") if isinstance(body, dict) else None
    if not errors:
        return ""
    first = errors[0] if isinstance(errors, list) and errors else errors
    if isinstance(first, (list, tuple)):
        code = str(first[0]) if first else "ERROR"
        msg = str(first[1]) if len(first) > 1 else ""
        return f"{msg or code} [{code}]"
    return str(first)


def atom_entries(xml: str, limit: int) -> list[dict]:
    """Pull entries out of an Atom feed. Module level so it can be unit-tested."""
    out: list[dict] = []
    for block in re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL)[:limit]:
        def grab(tag: str, b: str = block) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", b, re.DOTALL)
            return _strip_html(m.group(1), 4000) if m else ""
        link = re.search(r'<link[^>]*href="([^"]+)"', block)
        out.append({"title": grab("title"), "author": grab("name"),
                    "updated": grab("updated")[:10],
                    "link": link.group(1) if link else "",
                    "summary": grab("content")})
    return out


def register_social_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    # ─── REDDIT (Atom — the JSON API is blocked, see module docstring) ────────

    async def _reddit_feed(path: str, params: dict) -> list[dict]:
        # Reddit throttles unauthenticated clients hard — a handful of requests in
        # quick succession is enough to earn a 429 on every endpoint for a while,
        # including ones that answered a moment earlier. Space our own calls so a
        # chatty agent does not burn the allowance in one turn.
        async with _REDDIT_GATE:
            wait = _REDDIT_MIN_INTERVAL - (asyncio.get_running_loop().time() - _reddit_last[0])
            if wait > 0:
                await asyncio.sleep(wait)
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                         headers={"User-Agent": UA}) as c:
                r = await c.get(f"https://www.reddit.com{path}", params=params)
            _reddit_last[0] = asyncio.get_running_loop().time()
        r.raise_for_status()
        return atom_entries(r.text, int(params.get("limit", 10)))

    async def _reddit_call(method: str, path: str, *, params: dict | None = None,
                           data: dict | None = None,
                           account: str = "") -> dict | list | None:
        """One authenticated call to oauth.reddit.com, or None when not logged in.

        ``account`` picks which login answers; empty uses the default one. GET and
        POST share this because they share the part that is easy to get wrong:
        the single 401 retry against a token that expired between the check and
        the call.
        """
        from core import reddit_accounts as ra

        token = await reddit_token(account)
        if not token:
            return None
        acct = ra.resolve(_SOCIAL_ROOT, account)
        aid = acct["id"] if acct else ""

        async def _send(tok: str):
            url = f"https://oauth.reddit.com{path}"
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                         headers={"User-Agent": UA,
                                                  "Authorization": f"bearer {tok}"}) as c:
                if method == "POST":
                    return await c.post(url, data=data or {}, params=params or {})
                return await c.get(url, params=params or {})

        r = await _send(token)
        if r.status_code == 401:
            # The token expired early, or the app's password changed. One retry
            # with a fresh token beats surfacing an auth error for something the
            # user cannot see or act on.
            forget_reddit_token(aid)
            token = await reddit_token(account)
            if not token:
                return None
            r = await _send(token)
        r.raise_for_status()
        return r.json()

    async def _reddit_api(path: str, params: dict | None = None,
                          account: str = "") -> dict | list | None:
        return await _reddit_call("GET", path, params=params, account=account)

    def _from_listing(body: dict | None) -> list[dict]:
        """Reddit's JSON listing into the same rows the Atom path produces.

        The extra fields — score, comments, subreddit — are the whole reason to
        authenticate: the feeds do not carry them, so an anonymous agent cannot
        tell a post with three upvotes from one with thirty thousand.
        """
        rows: list[dict] = []
        for child in ((body or {}).get("data") or {}).get("children") or []:
            d = (child or {}).get("data") or {}
            if not isinstance(d, dict):
                continue
            created = d.get("created_utc")
            import datetime as _dt
            when = ""
            if created:
                try:
                    when = _dt.datetime.fromtimestamp(
                        float(created), _dt.timezone.utc).strftime("%Y-%m-%d")
                except (OSError, OverflowError, ValueError):
                    when = ""
            rows.append({
                "title": _strip_html(d.get("title") or d.get("link_title") or "", 300),
                "author": f"/u/{d['author']}" if d.get("author") else "",
                "updated": when,
                "link": "https://www.reddit.com" + (d.get("permalink") or "")
                        if d.get("permalink") else (d.get("url") or ""),
                "summary": _strip_html(d.get("selftext") or d.get("body") or "", 4000),
                "score": d.get("score"),
                "comments": d.get("num_comments"),
                "subreddit": d.get("subreddit") or "",
            })
        return rows

    def _render(header: str, entries: list[dict]) -> str:
        lines = [f"## {header}", ""]
        for e in entries:
            lines.append(f"- **{e['title']}**")
            bits = [e.get("author") or "", e.get("updated") or ""]
            if e.get("subreddit"):
                bits.insert(0, f"r/{e['subreddit']}")
            if e.get("score") is not None:
                bits.append(f"▲ {_fmt_count(e['score'])}")
            if e.get("comments") is not None:
                bits.append(f"💬 {_fmt_count(e['comments'])}")
            meta = " · ".join(x for x in bits if x)
            if meta:
                lines.append(f"  {meta}")
            if e.get("link"):
                lines.append(f"  {e['link']}")
        return NL.join(lines)

    def _note() -> str:
        """Told once per result, so the gap is visible rather than mysterious."""
        return ("" if reddit_configured() else
                NL + NL + "_Reading Reddit anonymously — scores, comment counts and "
                "your own subscriptions need a script app (Settings → Reddit)._")

    async def _reddit_entries(api_path: str, api_params: dict,
                              feed_path: str, feed_params: dict) -> list[dict]:
        """Rows for one listing: authenticated JSON if we have a login, else Atom.

        One helper rather than a branch in each tool, so no tool can accidentally
        stay anonymous after a login is configured.
        """
        body = await _reddit_api(api_path, api_params)
        if body is not None:
            return _from_listing(body if isinstance(body, dict) else {})
        return await _reddit_feed(feed_path, feed_params)

    class RedditSubInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        subreddit: str = Field(..., description="Subreddit name, without r/", min_length=1, max_length=60)
        sort: str = Field(default="hot", description="hot | new | top | rising")
        limit: int = Field(default=10, ge=1, le=50)

    @mcp.tool(name="reddit_subreddit_posts", annotations={"readOnlyHint": True})
    async def reddit_subreddit_posts(params: RedditSubInput) -> str:
        """Read posts from a subreddit (hot/new/top/rising).

        Works without an account; with one, results carry score and comment
        counts and private/subscriber-only subreddits you can see are readable.
        """
        sub = params.subreddit.strip().lstrip("/").removeprefix("r/")
        sort = params.sort if params.sort in ("hot", "new", "top", "rising") else "hot"
        try:
            entries = await _reddit_entries(
                f"/r/{sub}/{sort}", {"limit": params.limit},
                f"/r/{sub}/{sort}.rss", {"limit": params.limit})
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                return f"Error: r/{sub} is private, banned or does not exist."
            if e.response.status_code == 429:
                return "Error: Reddit is rate-limiting this server. Try again shortly."
            return _handle_error(e, "Reddit")
        except Exception as e:
            return _handle_error(e, "Reddit")
        if not entries:
            return f"No posts found in r/{sub}."
        return _render(f"r/{sub} — {sort}", entries) + _note()

    class RedditSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search terms", min_length=1, max_length=200)
        subreddit: str = Field(default="", description="Restrict to one subreddit (optional)", max_length=60)
        sort: str = Field(default="relevance", description="relevance | hot | top | new | comments")
        limit: int = Field(default=10, ge=1, le=50)

    @mcp.tool(name="reddit_search", annotations={"readOnlyHint": True})
    async def reddit_search(params: RedditSearchInput) -> str:
        """Search Reddit, optionally within one subreddit."""
        sub = params.subreddit.strip().lstrip("/").removeprefix("r/")
        path = f"/r/{sub}/search.rss" if sub else "/search.rss"
        q: dict = {"q": params.query, "sort": params.sort, "limit": params.limit}
        if sub:
            q["restrict_sr"] = "on"
        try:
            entries = await _reddit_entries(
                f"/r/{sub}/search" if sub else "/search", q, path, q)
        except Exception as e:
            return _handle_error(e, "Reddit")
        if not entries:
            return f"No Reddit results for '{params.query}'."
        where = f" in r/{sub}" if sub else ""
        return _render(f"Reddit search: '{params.query}'{where}", entries) + _note()

    class RedditCommentsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        permalink: str = Field(..., description="Post permalink or full URL from a search result",
                               min_length=3, max_length=400)
        limit: int = Field(default=15, ge=1, le=100)

    @mcp.tool(name="reddit_post_comments", annotations={"readOnlyHint": True})
    async def reddit_post_comments(params: RedditCommentsInput) -> str:
        """Read the comments on a Reddit post, given its permalink."""
        path = re.sub(r"^https?://(www\.|old\.)?reddit\.com", "", params.permalink.strip())
        if not path.startswith("/"):
            path = "/" + path
        path = path.rstrip("/")
        try:
            # The comments endpoint answers with *two* listings — the post, then
            # its comments — so it cannot go through _reddit_entries.
            body = await _reddit_api(path, {"limit": params.limit})
            if isinstance(body, list) and len(body) > 1:
                entries = _from_listing(body[1])
            elif body is not None:
                entries = _from_listing(body if isinstance(body, dict) else {})
            else:
                entries = await _reddit_feed(f"{path}.rss", {"limit": params.limit})
        except Exception as e:
            return _handle_error(e, "Reddit")
        if not entries:
            return "No comments found — check the permalink."
        lines = ["## Reddit thread", ""]
        for e in entries:
            score = f" (▲ {_fmt_count(e['score'])})" if e.get("score") is not None else ""
            lines.append(f"- **{e['author'] or '?'}**{score}: "
                         f"{(e['summary'] or e['title'])[:400]}")
        return NL.join(lines) + _note()

    # ─── REDDIT AS THE LOGGED-IN USER ────────────────────────────────────────
    #
    # These have no anonymous equivalent — they are the account's own view. A
    # research agent that can read your subscriptions and saved posts is working
    # from what *you* follow, rather than guessing at subreddit names.

    _NEEDS_LOGIN = ("Error: this needs a Reddit login. Add a script app "
                    "(https://www.reddit.com/prefs/apps) under Settings → Reddit: "
                    "client id, secret, your username and password.")

    @mcp.tool(name="reddit_accounts", annotations={"readOnlyHint": True})
    async def reddit_accounts_tool() -> str:
        """List the Reddit accounts Plutus can act as.

        Call this before any tool that takes an ``account`` when you need a
        specific identity — passing a name that does not exist is refused rather
        than quietly answered by the default account.
        """
        from core import reddit_accounts as ra

        accounts = ra.public_accounts(_SOCIAL_ROOT)
        if not accounts:
            return _NEEDS_LOGIN
        lines = [f"## Reddit accounts ({len(accounts)})", ""]
        for a in accounts:
            marks = []
            if a["is_default"]:
                marks.append("default")
            if a["from_env"]:
                marks.append("from .env")
            suffix = f" — {', '.join(marks)}" if marks else ""
            lines.append(f"- **{a['label']}** (u/{a['username']}) `{a['id']}`{suffix}")
        lines += ["", "Pass `account` to reddit_me, reddit_my_subreddits, "
                      "reddit_home_feed or reddit_my_posts to use one of these."]
        return NL.join(lines)

    class RedditWhoInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        account: str = Field(default="", max_length=80,
                             description="Which Reddit account to check. Empty = the default.")

    @mcp.tool(name="reddit_me", annotations={"readOnlyHint": True})
    async def reddit_me(params: RedditWhoInput) -> str:
        """Who Plutus is logged in to Reddit as, with karma and account age."""
        bad = reddit_auth_error(params.account)
        if bad:
            return f"Error: {bad}"
        try:
            me = await _reddit_api("/api/v1/me", account=params.account)
        except Exception as e:
            return _handle_error(e, "Reddit")
        if not isinstance(me, dict):
            return _NEEDS_LOGIN
        import datetime as _dt
        created = me.get("created_utc")
        since = ""
        if created:
            try:
                since = _dt.datetime.fromtimestamp(
                    float(created), _dt.timezone.utc).strftime("%Y-%m-%d")
            except (OSError, OverflowError, ValueError):
                since = ""
        return NL.join([
            "## Reddit account", "",
            f"**u/{me.get('name', '?')}**",
            f"- Post karma: {_fmt_count(me.get('link_karma'))}",
            f"- Comment karma: {_fmt_count(me.get('comment_karma'))}",
            f"- Member since: {since or '—'}",
            f"- Gold: {'yes' if me.get('is_gold') else 'no'}"
            f" · Mod: {'yes' if me.get('is_mod') else 'no'}",
        ])

    class RedditLimitInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        limit: int = Field(default=25, ge=1, le=100)
        account: str = Field(default="", max_length=80,
                             description="Which Reddit account to use "
                                         "(label, username or id). Empty = the default.")

    @mcp.tool(name="reddit_my_subreddits", annotations={"readOnlyHint": True})
    async def reddit_my_subreddits(params: RedditLimitInput) -> str:
        """List the subreddits this Reddit account subscribes to."""
        bad = reddit_auth_error(params.account)
        if bad:
            return f"Error: {bad}"
        try:
            body = await _reddit_api("/subreddits/mine/subscriber",
                                     {"limit": params.limit}, account=params.account)
        except Exception as e:
            return _handle_error(e, "Reddit")
        if body is None:
            return _NEEDS_LOGIN
        rows = ((body or {}).get("data") or {}).get("children") or []
        if not rows:
            return "This account is not subscribed to any subreddits."
        lines = [f"## Subscribed subreddits ({len(rows)})", ""]
        for child in rows:
            d = (child or {}).get("data") or {}
            subs = _fmt_count(d.get("subscribers"))
            lines.append(f"- **r/{d.get('display_name', '?')}** — {subs} members")
            if d.get("public_description"):
                lines.append(f"  {_strip_html(d['public_description'], 140)}")
        return NL.join(lines)

    @mcp.tool(name="reddit_home_feed", annotations={"readOnlyHint": True})
    async def reddit_home_feed(params: RedditLimitInput) -> str:
        """This account's Reddit front page — posts from the subreddits it follows."""
        bad = reddit_auth_error(params.account)
        if bad:
            return f"Error: {bad}"
        try:
            body = await _reddit_api("/best", {"limit": params.limit},
                                     account=params.account)
        except Exception as e:
            return _handle_error(e, "Reddit")
        if body is None:
            return _NEEDS_LOGIN
        entries = _from_listing(body if isinstance(body, dict) else {})
        if not entries:
            return "Nothing on the front page right now."
        return _render("Your Reddit front page", entries)

    class RedditSavedInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        limit: int = Field(default=25, ge=1, le=100)
        kind: str = Field(default="saved", description="saved | upvoted | submitted")
        account: str = Field(default="", max_length=80,
                             description="Which Reddit account to use "
                                         "(label, username or id). Empty = the default.")

    @mcp.tool(name="reddit_my_posts", annotations={"readOnlyHint": True})
    async def reddit_my_posts(params: RedditSavedInput) -> str:
        """This account's saved, upvoted or submitted Reddit posts.

        Saved posts are a research bookmark list an agent can actually work from.
        """
        bad = reddit_auth_error(params.account)
        if bad:
            return f"Error: {bad}"
        kind = params.kind if params.kind in ("saved", "upvoted", "submitted") else "saved"
        # The *resolved* account's username, not cfg.reddit_username: with
        # several logins the env one is rarely the one being asked about, and
        # the old code would have fetched the wrong person's saved posts.
        from core import reddit_accounts as ra
        acct = ra.resolve(_SOCIAL_ROOT, params.account)
        if not acct:
            return _NEEDS_LOGIN
        user = acct["username"]
        try:
            body = await _reddit_api(f"/user/{user}/{kind}", {"limit": params.limit},
                                     account=params.account)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                return (f"Error: Reddit refused to show '{kind}' — the script app's "
                        "scope does not cover it, or the list is private.")
            return _handle_error(e, "Reddit")
        except Exception as e:
            return _handle_error(e, "Reddit")
        if body is None:
            return _NEEDS_LOGIN
        entries = _from_listing(body if isinstance(body, dict) else {})
        if not entries:
            return f"Nothing in {kind}."
        return _render(f"u/{user} — {kind}", entries)

    # ─── REDDIT (writing) ─────────────────────────────────────────────────────
    #
    # These are the only Reddit tools that put something in front of other
    # people, so they are annotated not-read-only and carry a "submit"/"comment"
    # name — `core.agent_permissions.is_outward` picks both up, which means an
    # agent needs the *publish* switch on, not merely write, before it can reach
    # them. They are also safety level 2, so no smoke test ever fires one.

    async def _acting_as(account: str) -> "tuple[dict | None, str]":
        """The account a write will post as, or an error explaining why not.

        Writes never fall back to anonymous — there is no anonymous write — and
        never fall back to *a different account* than the one that was named.
        """
        from core import reddit_accounts as ra

        bad = reddit_auth_error(account)
        if bad:
            return None, f"Error: {bad}"
        acct = ra.resolve(_SOCIAL_ROOT, account)
        if not acct:
            return None, _NEEDS_LOGIN
        if not await reddit_token(account):
            return None, (f"Error: could not log in as u/{acct['username']}. Check that "
                          "account's script-app credentials in Settings → Reddit.")
        return acct, ""

    class RedditSubmitInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        subreddit: str = Field(..., description="Subreddit to post to, without r/",
                               min_length=1, max_length=60)
        title: str = Field(..., description="Post title", min_length=1, max_length=300)
        text: str = Field(default="", description="Body for a text post (markdown)",
                          max_length=40000)
        url: str = Field(default="", description="Link for a link post. Use text OR url, not both.",
                         max_length=2000)
        account: str = Field(default="", description="Which Reddit account posts. "
                                                     "Empty uses the default one.",
                             max_length=60)

    @mcp.tool(name="reddit_submit",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def reddit_submit(params: RedditSubmitInput) -> str:
        """Post to a subreddit as one of your Reddit accounts (text or link post).

        This is public and immediate. Reddit enforces its own per-account rate
        limit and subreddit rules — if the post is rejected the reason is
        reported, never swallowed.
        """
        if bool(params.text) == bool(params.url):
            return ("Error: give either `text` (a self post) or `url` (a link post), "
                    "not both and not neither.")
        acct, err = await _acting_as(params.account)
        if err:
            return err
        if params.url:
            # Same screen the federated `instance` arguments get: `url` is
            # model-supplied, and Reddit's crawler will fetch whatever we hand it.
            why = await _screen(params.url)
            if why:
                return f"Error: refusing to submit that link — {why}"

        sub = params.subreddit.strip().lstrip("/").removeprefix("r/")
        payload = {"sr": sub, "title": params.title, "api_type": "json",
                   "kind": "self" if params.text else "link"}
        payload["text" if params.text else "url"] = params.text or params.url
        try:
            body = await _reddit_call("POST", "/api/submit", data=payload,
                                      account=params.account)
        except Exception as e:
            return _handle_error(e, "Reddit")
        if body is None:
            return _NEEDS_LOGIN
        bad = reddit_api_error(body)
        if bad:
            return f"Error: Reddit rejected the post — {bad}"
        data = ((body or {}).get("json") or {}).get("data") or {}
        link = data.get("url") or ""
        return (f"Posted to r/{sub} as u/{acct['username']}: **{params.title}**"
                + (f"{NL}{link}" if link else ""))

    class RedditCommentInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        target: str = Field(..., description="What to reply to: a permalink, a "
                                             "fullname like t3_abc123 (post) or "
                                             "t1_abc123 (comment), or a post id",
                            min_length=1, max_length=300)
        text: str = Field(..., description="Comment body (markdown)",
                          min_length=1, max_length=10000)
        account: str = Field(default="", description="Which Reddit account comments. "
                                                     "Empty uses the default one.",
                             max_length=60)

    @mcp.tool(name="reddit_comment",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def reddit_comment(params: RedditCommentInput) -> str:
        """Reply to a Reddit post or comment as one of your accounts.

        `target` takes the permalink you were reading, so you do not have to
        translate it into a fullname first.
        """
        thing = reddit_thing_id(params.target)
        if not thing:
            return (f"Error: could not tell what '{params.target}' refers to. Give a "
                    "reddit.com permalink, or a fullname like t3_abc123 (post) or "
                    "t1_abc123 (comment).")
        acct, err = await _acting_as(params.account)
        if err:
            return err
        try:
            body = await _reddit_call("POST", "/api/comment", account=params.account,
                                      data={"thing_id": thing, "text": params.text,
                                            "api_type": "json"})
        except Exception as e:
            return _handle_error(e, "Reddit")
        if body is None:
            return _NEEDS_LOGIN
        bad = reddit_api_error(body)
        if bad:
            return f"Error: Reddit rejected the comment — {bad}"
        things = (((body or {}).get("json") or {}).get("data") or {}).get("things") or []
        perma = ((things[0] or {}).get("data") or {}).get("permalink", "") if things else ""
        kind = "comment" if thing.startswith("t1_") else "post"
        return (f"Replied to {kind} `{thing}` as u/{acct['username']}."
                + (f"{NL}https://www.reddit.com{perma}" if perma else ""))

    # ─── HACKER NEWS ──────────────────────────────────────────────────────────

    class HnTopInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        feed: str = Field(default="top", description="top | new | best | ask | show | job")
        limit: int = Field(default=10, ge=1, le=30)

    @mcp.tool(name="hackernews_stories", annotations={"readOnlyHint": True})
    async def hackernews_stories(params: HnTopInput) -> str:
        """Current Hacker News front page (or new/best/ask/show/jobs)."""
        feeds = {"top": "topstories", "new": "newstories", "best": "beststories",
                 "ask": "askstories", "show": "showstories", "job": "jobstories"}
        key = feeds.get(params.feed, "topstories")
        try:
            ids = await _get_json(f"https://hacker-news.firebaseio.com/v0/{key}.json")
            ids = (ids or [])[: params.limit]
            items = await asyncio.gather(*[
                _get_json(f"https://hacker-news.firebaseio.com/v0/item/{i}.json") for i in ids
            ], return_exceptions=True)
        except Exception as e:
            return _handle_error(e, "Hacker News")
        lines = [f"## Hacker News — {params.feed}", ""]
        for it in items:
            if isinstance(it, Exception) or not isinstance(it, dict):
                continue
            discussion = f"https://news.ycombinator.com/item?id={it.get('id')}"
            lines.append(f"- **{it.get('title', '(no title)')}**")
            lines.append(f"  {_fmt_count(it.get('score'))} pts · "
                         f"{_fmt_count(it.get('descendants'))} comments · by {it.get('by', '?')}")
            lines.append(f"  {it.get('url') or discussion}")
            if it.get("url"):
                lines.append(f"  discussion: {discussion}")
        return NL.join(lines)

    class HnSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., min_length=1, max_length=200)
        sort: str = Field(default="relevance", description="relevance | date")
        limit: int = Field(default=10, ge=1, le=50)

    @mcp.tool(name="hackernews_search", annotations={"readOnlyHint": True})
    async def hackernews_search(params: HnSearchInput) -> str:
        """Search Hacker News history (Algolia). Good for 'what did HN say about X'."""
        try:
            path = "search_by_date" if params.sort == "date" else "search"
            data = await _get_json(f"https://hn.algolia.com/api/v1/{path}",
                                   {"query": params.query, "hitsPerPage": params.limit,
                                    "tags": "story"})
        except Exception as e:
            return _handle_error(e, "Hacker News")
        hits = data.get("hits") or []
        if not hits:
            return f"No Hacker News results for '{params.query}'."
        lines = [f"## Hacker News search: '{params.query}'", ""]
        for h in hits:
            lines.append(f"- **{h.get('title') or h.get('story_title') or '(no title)'}**")
            lines.append(f"  {_fmt_count(h.get('points'))} pts · "
                         f"{_fmt_count(h.get('num_comments'))} comments · {(h.get('created_at') or '')[:10]}")
            lines.append(f"  https://news.ycombinator.com/item?id={h.get('objectID')}")
        return NL.join(lines)

    # ─── LEMMY (fediverse link aggregator) ────────────────────────────────────

    class LemmyInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        community: str = Field(default="", description="Community name, e.g. 'technology' (optional)", max_length=80)
        instance: str = Field(default=DEFAULT_LEMMY, description="Lemmy instance host", max_length=100)
        sort: str = Field(default="Hot", description="Hot | New | TopDay | TopWeek | Active")
        limit: int = Field(default=10, ge=1, le=50)

    @mcp.tool(name="lemmy_posts", annotations={"readOnlyHint": True})
    async def lemmy_posts(params: LemmyInput) -> str:
        """Read posts from a Lemmy instance — the fediverse's Reddit equivalent."""
        host = _host_ok(params.instance) or DEFAULT_LEMMY
        try:
            q: dict = {"sort": params.sort, "limit": params.limit, "type_": "All"}
            if params.community.strip():
                q["community_name"] = params.community.strip()
            data = await _get_json(f"https://{host}/api/v3/post/list", q, screen=True)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return _handle_error(e, "Lemmy")
        posts = data.get("posts") or []
        if not posts:
            return f"No posts found on {host}."
        head = f"Lemmy — {host}" + (f" · c/{params.community}" if params.community else "")
        lines = [f"## {head}", ""]
        for entry in posts:
            p = entry.get("post", {})
            counts = entry.get("counts", {})
            lines.append(f"- **{p.get('name', '(no title)')}**")
            lines.append(f"  {_fmt_count(counts.get('score'))} pts · "
                         f"{_fmt_count(counts.get('comments'))} comments · "
                         f"c/{entry.get('community', {}).get('name', '?')}")
            lines.append(f"  {p.get('ap_id') or p.get('url') or ''}")
        return NL.join(lines)

    # ─── MASTODON ─────────────────────────────────────────────────────────────

    class MastodonInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        tag: str = Field(default="", description="Hashtag without '#'. Blank = the public timeline.", max_length=80)
        instance: str = Field(default=DEFAULT_MASTODON, description="Mastodon instance host", max_length=100)
        limit: int = Field(default=10, ge=1, le=40)

    @mcp.tool(name="mastodon_timeline", annotations={"readOnlyHint": True})
    async def mastodon_timeline(params: MastodonInput) -> str:
        """Read a Mastodon public timeline, or everything under one hashtag."""
        host = _host_ok(params.instance) or DEFAULT_MASTODON
        tag = params.tag.strip().lstrip("#")
        try:
            url = (f"https://{host}/api/v1/timelines/tag/{tag}" if tag
                   else f"https://{host}/api/v1/timelines/public")
            posts = await _get_json(url, {"limit": params.limit}, screen=True)
        except PermissionError as e:
            return f"Error: {e}"
        except httpx.HTTPStatusError as e:
            # 422 here is not a bad request — it is the instance saying it does
            # not serve anonymous reads. "HTTP 422" sends someone hunting for a
            # bug in their arguments instead of changing one field.
            if e.response is not None and e.response.status_code in (401, 422):
                return (f"Error: {host} does not allow reading its timeline without an "
                        f"account. Set `instance` to one that does — mstdn.social, "
                        f"fosstodon.org and mas.to all work.")
            return _handle_error(e, "Mastodon")
        except Exception as e:
            return _handle_error(e, "Mastodon")
        if not posts:
            return f"Nothing on {host}" + (f" for #{tag}." if tag else ".")
        lines = [f"## Mastodon — {host}" + (f" · #{tag}" if tag else " · public"), ""]
        for p in posts:
            acct = p.get("account", {})
            lines.append(f"- **@{acct.get('acct', '?')}** "
                         f"({_fmt_count(p.get('favourites_count'))} ★, "
                         f"{_fmt_count(p.get('reblogs_count'))} ↺)")
            lines.append(f"  {_strip_html(p.get('content', ''), 280)}")
            lines.append(f"  {p.get('url', '')}")
        return NL.join(lines)

    # ─── BLUESKY ──────────────────────────────────────────────────────────────

    class BlueskyInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search terms", min_length=1, max_length=200)
        limit: int = Field(default=10, ge=1, le=50)

    @mcp.tool(name="bluesky_search", annotations={"readOnlyHint": True})
    async def bluesky_search(params: BlueskyInput) -> str:
        """Search public Bluesky posts (no account needed)."""
        try:
            # api.bsky.app, not public.api.bsky.app — the latter 403s this endpoint.
            data = await _get_json("https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                                   {"q": params.query, "limit": params.limit})
        except Exception as e:
            return _handle_error(e, "Bluesky")
        posts = data.get("posts") or []
        if not posts:
            return f"No Bluesky posts for '{params.query}'."
        lines = [f"## Bluesky: '{params.query}'", ""]
        for p in posts:
            handle = p.get("author", {}).get("handle", "?")
            rec = p.get("record", {}) or {}
            rkey = (p.get("uri", "").rsplit("/", 1) or [""])[-1]
            lines.append(f"- **@{handle}** ({_fmt_count(p.get('likeCount'))} ♥, "
                         f"{_fmt_count(p.get('repostCount'))} ↺)")
            lines.append(f"  {_strip_html(rec.get('text', ''), 280)}")
            lines.append(f"  https://bsky.app/profile/{handle}/post/{rkey}")
        return NL.join(lines)

    # ─── STACK EXCHANGE ───────────────────────────────────────────────────────

    class StackInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., min_length=1, max_length=200)
        site: str = Field(default="stackoverflow", description="stackoverflow | superuser | serverfault | askubuntu …")
        limit: int = Field(default=10, ge=1, le=30)

    @mcp.tool(name="stackexchange_search", annotations={"readOnlyHint": True})
    async def stackexchange_search(params: StackInput) -> str:
        """Search Stack Overflow / Stack Exchange questions (anonymous quota)."""
        try:
            data = await _get_json("https://api.stackexchange.com/2.3/search/advanced", {
                "order": "desc", "sort": "relevance", "q": params.query,
                "site": params.site, "pagesize": params.limit, "filter": "default"})
        except Exception as e:
            return _handle_error(e, "Stack Exchange")
        items = data.get("items") or []
        if not items:
            return f"No {params.site} results for '{params.query}'."
        lines = [f"## {params.site}: '{params.query}'", ""]
        for i in items:
            mark = "✅" if i.get("is_answered") else "○"
            lines.append(f"- {mark} **{_html.unescape(i.get('title', ''))}**")
            lines.append(f"  score {_fmt_count(i.get('score'))} · "
                         f"{_fmt_count(i.get('answer_count'))} answers")
            lines.append(f"  {i.get('link', '')}")
        if data.get("quota_remaining") is not None and data["quota_remaining"] < 20:
            lines.append("")
            lines.append(f"_Anonymous quota nearly used: {data['quota_remaining']} left today._")
        return NL.join(lines)
