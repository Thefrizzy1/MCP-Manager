"""
tools/reach.py — the Agent Reach channels Plutus did not already have.

Agent Reach (github.com/Panniantong/Agent-Reach) is an installer and skill that
gives a coding agent the internet: for each platform it picks a working backend
(a CLI, a keyless API, an MCP server), and ``agent-reach doctor`` says which one
is serving each platform right now. Plutus already covered several of its
channels — YouTube through yt-dlp (tools/youtube.py), GitHub, Reddit, web search
and Firecrawl — so this module adds the rest that can work on a headless server,
and the doctor:

- **web_read** — Jina Reader (``r.jina.ai``): any page back as Markdown, rendered
  server-side by Jina. Keyless; ``JINA_API_KEY`` only raises the rate limit.
  Jina answers a browser User-Agent with a Cloudflare challenge, so it gets ours.
- **exa_search** — Exa's semantic web search. Keyless through Exa's hosted MCP
  endpoint (the same one Agent Reach calls through mcporter); with
  ``EXA_API_KEY`` it goes to the REST API, the key in a header.
- **rss_read** — RSS 2.0, RDF and Atom feeds. Plutus fetches these itself, so
  every redirect hop is SSRF-screened, the same rule web_fetch follows.
- **v2ex_*** and **bilibili_*** — public APIs, no login. Bilibili answers its
  search API only to a client holding the ``buvid3`` cookie its home page sets,
  and refuses the unsigned ``/view`` endpoint (412) while ``/wbi/view`` answers —
  verified live, which is why the paths below are what they are. Subtitles need a
  logged-in session and are not offered.
- **twitter_*** — X has no keyless read path, so this drives ``twitter-cli`` (the
  backend Agent Reach prefers) with the two cookies of a logged-in x.com session,
  exported by hand into ``TWITTER_AUTH_TOKEN`` / ``TWITTER_CT0``. twitter-cli
  falls back to reading cookies out of installed browsers when the explicit ones
  are missing or rejected; the child therefore runs with HOME and the app-data
  directories pointed at an empty sandbox, so that fallback finds nothing. It is
  never started without both cookies.
- **reach_doctor** — every Agent Reach platform, the Plutus tools and backend
  that serve it, and what is missing. ``probe`` checks the keyless ones live.

Not here, deliberately: XiaoHongShu, Facebook, Instagram, Xueqiu, Boss Zhipin.
Agent Reach reaches those through OpenCLI driving a *logged-in desktop Chrome*;
a server in a container has no such browser. The doctor says so rather than
pretending. LinkedIn's public pages read through web_read, which is Agent
Reach's own fallback for it.
"""

import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from client import _handle_error
from config import cfg
from tools.social import _fmt_count, _strip_html

_ROOT = Path(__file__).resolve().parents[1]
NL = "\n"

# Jina and V2EX answer a plain client UA; Jina serves a browser UA a Cloudflare
# challenge page instead of the article. Bilibili is the opposite — it wants a
# browser and a Referer.
UA = "PlutusMCP/1.0 (homelab MCP server)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

JINA = "https://r.jina.ai/"
EXA_MCP = "https://mcp.exa.ai/mcp"
EXA_API = "https://api.exa.ai/search"
V2EX = "https://www.v2ex.com/api"
BILI_API = "https://api.bilibili.com"

FEED_MAX_BYTES = 3 * 1024 * 1024
_MAX_REDIRECTS = 5
TWITTER_TIMEOUT = 90


# ── small parsers (module level so they can be unit-tested) ──────────────────

def clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def parse_tweet_id(ref: str) -> str:
    """A tweet id from an id or an x.com / twitter.com status URL."""
    ref = (ref or "").strip()
    if re.fullmatch(r"\d{5,25}", ref):
        return ref
    u = urlparse(ref if "://" in ref else f"https://{ref}")
    host = (u.hostname or "").lower()
    if host.removeprefix("www.").removeprefix("mobile.") in ("x.com", "twitter.com"):
        m = re.search(r"/status(?:es)?/(\d{5,25})", u.path)
        if m:
            return m.group(1)
    raise ValueError(f"not a tweet id or x.com status URL: {ref[:80]!r}")


def parse_handle(ref: str) -> str:
    """An X handle from '@name', 'name' or a profile URL."""
    ref = (ref or "").strip()
    if "/" in ref:
        u = urlparse(ref if "://" in ref else f"https://{ref}")
        host = (u.hostname or "").lower().removeprefix("www.").removeprefix("mobile.")
        if host not in ("x.com", "twitter.com"):
            raise ValueError(f"not an x.com profile: {ref[:80]!r}")
        ref = (u.path.strip("/").split("/") or [""])[0]
    ref = ref.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", ref):
        raise ValueError(f"not an X handle: {ref[:80]!r}")
    return ref


def parse_bvid(ref: str) -> str:
    """A Bilibili BV id from the id itself or a video URL."""
    m = re.search(r"\b(BV[0-9A-Za-z]{10})\b", ref or "")
    if not m:
        raise ValueError(f"not a Bilibili BV id or video URL: {(ref or '')[:80]!r}")
    return m.group(1)


def parse_v2ex_topic(ref: str) -> int:
    ref = (ref or "").strip()
    m = re.fullmatch(r"\d{1,9}", ref) or re.search(r"v2ex\.com/t/(\d{1,9})", ref)
    if not m:
        raise ValueError(f"not a V2EX topic id or URL: {ref[:80]!r}")
    return int(m.group(1) if m.lastindex else m.group(0))


def _local(tag: str) -> str:
    """'{http://www.w3.org/2005/Atom}entry' -> 'entry'."""
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child(el, *names: str):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el, *names: str) -> str:
    c = _child(el, *names)
    return (c.text or "").strip() if c is not None and c.text else ""


def feed_entries(xml: str | bytes, limit: int) -> tuple[str, list[dict]]:
    """(feed title, entries) from RSS 2.0, RSS 1.0/RDF or Atom.

    Namespace-agnostic on purpose: real feeds mix Atom, Dublin Core, content: and
    media: namespaces freely, and matching on local names reads all of them.
    Junk in returns ("", []) rather than raising — a feed URL that turned out to
    be an HTML page is a normal outcome, not an exception.
    """
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError, TypeError):
        return "", []
    kind = _local(root.tag)
    if kind == "feed":                                    # Atom
        title = _text(root, "title")
        items = [e for e in root if _local(e.tag) == "entry"]
    elif kind in ("rss", "rdf"):
        channel = _child(root, "channel")
        title = _text(channel, "title") if channel is not None else ""
        items = [e for e in (channel if channel is not None and kind == "rss" else root)
                 if _local(e.tag) == "item"]
    else:
        return "", []
    out: list[dict] = []
    for it in items[:limit]:
        link = ""
        for c in it:
            if _local(c.tag) != "link":
                continue
            href = c.get("href")
            if href and c.get("rel", "alternate") == "alternate":
                link = href
                break
            if not href and c.text:
                link = c.text.strip()
                break
        author = _text(it, "creator", "author")
        a = _child(it, "author")
        if a is not None and len(a):
            author = _text(a, "name") or author
        summary = (_text(it, "description", "summary") or _text(it, "encoded", "content"))
        out.append({
            "title": _strip_html(_text(it, "title"), 300),
            "link": link,
            "date": _text(it, "pubdate", "published", "updated", "date")[:32],
            "author": _strip_html(author, 80),
            "summary": _strip_html(summary, 400),
        })
    return _strip_html(title, 200), out


# ── HTTP helpers ─────────────────────────────────────────────────────────────

async def _screen(url: str) -> str:
    from core.ssrf_guard import screen_url
    return await asyncio.to_thread(screen_url, url) or ""


async def fetch_screened(url: str, *, cap: int = FEED_MAX_BYTES,
                         headers: dict | None = None) -> tuple[str, bytes, str]:
    """GET a caller-chosen URL with every redirect hop screened.

    Returns (final url, body up to ``cap`` bytes, content-type). Raises
    PermissionError when a hop is refused by the SSRF guard.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=False,
                                 headers={"User-Agent": UA, **(headers or {})}) as c:
        for _ in range(_MAX_REDIRECTS + 1):
            blocked = await _screen(url)
            if blocked:
                raise PermissionError(blocked)
            async with c.stream("GET", url) as r:
                if r.is_redirect:
                    nxt = r.next_request
                    if nxt is None:
                        raise ValueError(f"redirect from {url} had no usable Location")
                    url = str(nxt.url)
                    continue
                r.raise_for_status()
                chunks, got = [], 0
                async for chunk in r.aiter_bytes():
                    chunks.append(chunk)
                    got += len(chunk)
                    if got >= cap:
                        break
                return url, b"".join(chunks)[:cap], r.headers.get("content-type", "")
    raise ValueError(f"too many redirects (>{_MAX_REDIRECTS})")


async def _get_json(url: str, params: dict | None = None, headers: dict | None = None):
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0), follow_redirects=True,
                                 headers={"User-Agent": UA, "Accept": "application/json",
                                          **(headers or {})}) as c:
        r = await c.get(url, params=params or {})
        r.raise_for_status()
        return r.json()


# Bilibili's search API answers -412 to a client without the buvid3 cookie its
# home page sets. Fetch it once and reuse it for an hour rather than loading
# bilibili.com in front of every call.
_BILI_COOKIES: dict = {"jar": None, "at": 0.0}
_BILI_LOCK = asyncio.Lock()


async def _bili(path: str, params: dict) -> dict:
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.bilibili.com/",
               "Accept": "application/json"}
    async with _BILI_LOCK:
        if _BILI_COOKIES["jar"] is None or time.time() - _BILI_COOKIES["at"] > 3600:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as c:
                await c.get("https://www.bilibili.com/")
                _BILI_COOKIES.update(jar=dict(c.cookies.items()), at=time.time())
        jar = dict(_BILI_COOKIES["jar"] or {})
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers,
                                 cookies=jar) as c:
        r = await c.get(f"{BILI_API}{path}", params=params)
    if r.status_code == 412:
        _BILI_COOKIES["jar"] = None          # a stale cookie is the usual cause
        raise RuntimeError("Bilibili refused the request (412, its anti-bot check). Try again in a minute.")
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in (0, None):
        raise RuntimeError(f"Bilibili answered code {body.get('code')}: {body.get('message', '')}")
    return body.get("data") or {}


def _date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _dur(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return str(seconds or "")
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── X / Twitter through twitter-cli ──────────────────────────────────────────

TWITTER_NOT_INSTALLED = (
    "X/Twitter is not set up: twitter-cli is not installed. The Docker image ships "
    "it; elsewhere run `pipx install twitter-cli` (or set TWITTER_CLI_COMMAND in "
    ".env to its path).")
TWITTER_NO_COOKIES = (
    "X/Twitter is not configured. Add TWITTER_AUTH_TOKEN and TWITTER_CT0 in "
    "Settings → X / Twitter: the `auth_token` and `ct0` cookies of a logged-in "
    "x.com session, exported with a cookie-editor browser extension. Plutus never "
    "reads browser cookies itself. Use a spare account — reads count against it.")


class TwitterError(RuntimeError):
    pass


def twitter_command() -> list[str]:
    raw = (cfg.twitter_cli_command or "").strip() or "twitter"
    if Path(raw).is_file():
        return [raw]
    parts = shlex.split(raw, posix=os.name != "nt")
    exe = shutil.which(parts[0]) if parts else None
    return [exe, *parts[1:]] if exe else []


def twitter_cookies_set() -> bool:
    return bool((cfg.twitter_auth_token or "").strip() and (cfg.twitter_ct0 or "").strip())


def twitter_env() -> tuple[dict[str, str], str]:
    """(child environment, working dir). Only the OS essentials, the two cookies,
    and every home/app-data location pointed at an empty sandbox — so
    twitter-cli's browser-cookie fallback has no browser profile to find."""
    from core.mcp_stdio import base_env

    home = _ROOT / "data" / "twitter-cli" / "home"
    dirs = {"HOME": home, "USERPROFILE": home, "APPDATA": home / "AppData" / "Roaming",
            "LOCALAPPDATA": home / "AppData" / "Local", "XDG_CONFIG_HOME": home / ".config",
            "XDG_DATA_HOME": home / ".local" / "share", "XDG_CACHE_HOME": home / ".cache"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    env = base_env({"TWITTER_AUTH_TOKEN": cfg.twitter_auth_token.strip(),
                    "TWITTER_CT0": cfg.twitter_ct0.strip(), "OUTPUT": "json",
                    "NO_COLOR": "1", **{k: str(v) for k, v in dirs.items()}},
                   drop=("TWITTER_BROWSER", "TWITTER_CHROME_PROFILE"))
    return env, str(home)


def _run_twitter_sync(args: list[str]) -> object:
    cmd = twitter_command()
    if not cmd:
        raise TwitterError(TWITTER_NOT_INSTALLED)
    if not twitter_cookies_set():
        raise TwitterError(TWITTER_NO_COOKIES)
    env, cwd = twitter_env()
    try:
        p = subprocess.run([*cmd, *args], capture_output=True, env=env, cwd=cwd,
                           timeout=TWITTER_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise TwitterError(f"twitter-cli did not answer within {TWITTER_TIMEOUT}s.")
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    try:
        body = json.loads(out) if out else None
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("ok") is False:
        err = body.get("error") or {}
        msg = str(err.get("message") or err)
        if any(w in msg.lower() for w in ("cookie", "401", "403", "auth")):
            msg += ("\n\nThe X cookies were rejected or have expired — export fresh "
                    "auth_token and ct0 values from a logged-in x.com session.")
        raise TwitterError(f"X/Twitter: {clip(msg, 600)}")
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    tail = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
    raise TwitterError("twitter-cli returned no JSON (exit %s). %s" % (p.returncode, " ".join(tail)[:400]))


async def run_twitter(args: list[str]) -> object:
    return await asyncio.to_thread(_run_twitter_sync, args)


def format_tweets(tweets: list, head: str) -> str:
    lines = [f"## {head}", ""]
    for t in tweets or []:
        if not isinstance(t, dict):
            continue
        a = t.get("author") or {}
        m = t.get("metrics") or {}
        handle = a.get("screenName") or "?"
        when = (t.get("createdAtISO") or t.get("createdAt") or "")[:16].replace("T", " ")
        rt = f" · ↺ by @{t['retweetedBy']}" if t.get("isRetweet") and t.get("retweetedBy") else ""
        lines.append(f"- **@{handle}** ({a.get('name', '')}) · {when}{rt}")
        lines.append(f"  ♥ {_fmt_count(m.get('likes'))} · ↺ {_fmt_count(m.get('retweets'))} · "
                     f"💬 {_fmt_count(m.get('replies'))} · 👁 {_fmt_count(m.get('views'))}")
        if t.get("articleTitle"):
            text = f"[Article] {t['articleTitle']}: {t.get('articleText') or ''}"
        else:
            text = t.get("text") or ""
        lines.append(f"  {clip(text.replace(NL, ' '), 500)}")
        q = t.get("quotedTweet")
        if isinstance(q, dict):
            lines.append(f"  > quoting @{(q.get('author') or {}).get('screenName', '?')}: "
                         f"{clip((q.get('text') or '').replace(NL, ' '), 200)}")
        lines.append(f"  https://x.com/{handle}/status/{t.get('id', '')}")
    if len(lines) == 2:
        lines.append("Nothing returned.")
    return NL.join(lines)


# ── the doctor ───────────────────────────────────────────────────────────────

def _yt_dlp_present() -> bool:
    import importlib.util
    return importlib.util.find_spec("yt_dlp") is not None


def _reddit_login() -> bool:
    try:
        from core import reddit_accounts
        return bool(reddit_accounts.list_accounts(_ROOT))
    except Exception:
        return bool(cfg.reddit_client_id)


def doctor_rows() -> list[dict]:
    """Every Agent Reach platform: status ∈ ready | setup | unavailable."""
    def row(platform, tools, backend, status, note=""):
        return {"platform": platform, "tools": tools, "backend": backend,
                "status": status, "note": note}

    tw_cmd, tw_cookies = bool(twitter_command()), twitter_cookies_set()
    if tw_cmd and tw_cookies:
        tw = row("X / Twitter", "twitter_search, twitter_user_posts, twitter_tweet",
                 "twitter-cli + your exported cookies", "ready",
                 "Not probed: a probe would spend the account's rate limit.")
    else:
        need = [w for w, ok in (("install twitter-cli", tw_cmd),
                                ("set TWITTER_AUTH_TOKEN + TWITTER_CT0", tw_cookies)) if not ok]
        tw = row("X / Twitter", "twitter_*", "twitter-cli", "setup", " and ".join(need))
    from tools.keywords import backend_status as kw_status
    kw = kw_status()
    opencli = ("Agent Reach serves this only through OpenCLI driving a logged-in desktop "
               "Chrome; a headless server has no such session.")
    return [
        row("Web pages", "web_read · web_fetch · firecrawl_scrape",
            "Jina Reader" + (" (key)" if cfg.jina_api_key else " (keyless)"), "ready"),
        row("Web search", "exa_search · web_search · google_search",
            "Exa" + (" REST (key)" if cfg.exa_api_key else " MCP (keyless)") + " · DuckDuckGo"
            + (" · Google CSE" if cfg.google_api_key and cfg.google_cse_id else ""), "ready"),
        row("YouTube", "youtube_* (watch, transcript, comments, channel tables…)",
            "yt-dlp" + (" + Data API key" if cfg.youtube_api_key else ""),
            "ready" if _yt_dlp_present() else "setup",
            "" if _yt_dlp_present() else "pip install yt-dlp"),
        row("GitHub", "github_*", "REST API",
            "ready", "token set" if cfg.github_token else "anonymous: 60 requests/hour; add GITHUB_TOKEN for more"),
        row("Reddit", "reddit_*", "oauth.reddit.com (your login)" if _reddit_login() else "public Atom feeds",
            "ready", "" if _reddit_login() else "titles and links only until a Reddit login is added"),
        tw,
        row("Bilibili", "bilibili_search · bilibili_video", "public web API", "ready",
            "no subtitles: those need a logged-in session"),
        row("V2EX", "v2ex_topics · v2ex_topic", "public API", "ready"),
        row("RSS / Atom", "rss_read", "direct fetch (SSRF-screened)", "ready"),
        row("Bluesky · Mastodon · Lemmy · HN · Stack Exchange", "bluesky_search, mastodon_timeline, …",
            "public APIs", "ready", "Plutus extras, not in Agent Reach"),
        row("Search volume", "keyword_volume", kw["backend"] or "—",
            "ready" if kw["backend"] else "setup", kw["note"]),
        row("LinkedIn", "web_read", "Jina Reader (public pages)", "ready",
            "public profiles and posts only — Agent Reach's own fallback"),
        row("XiaoHongShu · Facebook · Instagram · Xueqiu · Boss Zhipin", "—", "OpenCLI",
            "unavailable", opencli),
    ]


async def doctor_probes() -> dict[str, str]:
    """Live checks for the keyless channels. platform -> 'ok' or the failure."""
    async def jina():
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as c:
            r = await c.get(JINA + "https://example.com")
        r.raise_for_status()
        if "Example Domain" not in r.text:
            raise RuntimeError("unexpected body")

    async def exa():
        from core.mcp_client import McpHttpClient

        def go():
            with McpHttpClient(EXA_MCP, timeout=20) as c:
                c.initialize()
        await asyncio.to_thread(go)

    async def v2ex():
        if not await _get_json(f"{V2EX}/topics/hot.json"):
            raise RuntimeError("empty")

    async def bili():
        if not (await _bili("/x/web-interface/popular", {"ps": 1})).get("list"):
            raise RuntimeError("empty")

    checks = {"Web pages": jina, "Web search": exa, "V2EX": v2ex, "Bilibili": bili}
    results = await asyncio.gather(*(asyncio.wait_for(f(), 30) for f in checks.values()),
                                   return_exceptions=True)
    return {k: ("ok" if not isinstance(r, BaseException) else f"failed: {type(r).__name__} {str(r)[:80]}")
            for k, r in zip(checks, results)}


# ── tools ────────────────────────────────────────────────────────────────────

def register_reach_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    # ─── WEB PAGES (Jina Reader) ──────────────────────────────────────────────

    class WebReadInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        url: str = Field(..., description="Page URL", min_length=4, max_length=2000)
        max_chars: int = Field(default=8000, ge=200, le=50000)
        fresh: bool = Field(default=False, description="Bypass Jina's cache")

    @mcp.tool(name="web_read", annotations={"readOnlyHint": True})
    async def web_read(params: WebReadInput) -> str:
        """Read any web page as clean Markdown via Jina Reader — renders JavaScript,
        works on most articles, docs, LinkedIn/Medium posts. Keyless."""
        url = params.url if "://" in params.url else f"https://{params.url}"
        blocked = await _screen(url)
        if blocked:
            return f"Error: {blocked}"
        headers = {"User-Agent": UA, "Accept": "text/plain"}
        if cfg.jina_api_key:
            headers["Authorization"] = f"Bearer {cfg.jina_api_key}"
        if params.fresh:
            headers["X-No-Cache"] = "true"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), headers=headers) as c:
                r = await c.get(JINA + url)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                return ("Error: Jina Reader's keyless rate limit is used up. Wait a minute, add "
                        "JINA_API_KEY in Settings → Agent Reach, or use web_fetch / firecrawl_scrape.")
            return _handle_error(e, "Jina Reader")
        except Exception as e:
            return _handle_error(e, "Jina Reader")
        text = r.text.strip()
        if len(text) > params.max_chars:
            text = text[: params.max_chars] + f"\n\n… (clipped at {params.max_chars} chars)"
        return text or f"Jina Reader returned nothing for {url}."

    # ─── EXA SEARCH ───────────────────────────────────────────────────────────

    class ExaInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., min_length=1, max_length=500,
                           description="Natural-language query — describe the page you want")
        num_results: int = Field(default=5, ge=1, le=20)

    @mcp.tool(name="exa_search", annotations={"readOnlyHint": True})
    async def exa_search(params: ExaInput) -> str:
        """Exa semantic web search: finds pages by meaning, returns highlights from
        each. Strong for technical docs, research, code examples. Keyless."""
        if cfg.exa_api_key:
            try:
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.post(EXA_API, headers={"x-api-key": cfg.exa_api_key},
                                     json={"query": params.query, "numResults": params.num_results,
                                           "contents": {"highlights": {"numSentences": 3}}})
                r.raise_for_status()
                results = r.json().get("results") or []
            except Exception as e:
                return _handle_error(e, "Exa")
            if not results:
                return f"No Exa results for '{params.query}'."
            lines = [f"## Exa: '{params.query}'", ""]
            for res in results:
                lines.append(f"- **{res.get('title') or res.get('url')}**"
                             + (f" · {res['publishedDate'][:10]}" if res.get("publishedDate") else ""))
                lines.append(f"  {res.get('url', '')}")
                for h in (res.get("highlights") or [])[:3]:
                    lines.append(f"  > {clip(h.replace(NL, ' '), 300)}")
            return NL.join(lines)

        from core.mcp_client import McpHttpClient

        def call():
            with McpHttpClient(EXA_MCP, timeout=60) as c:
                return c.call_tool("web_search_exa", {"query": params.query,
                                                      "numResults": params.num_results})
        try:
            res = await asyncio.to_thread(call)
        except Exception as e:
            return _handle_error(e, "Exa")
        if res.get("is_error"):
            return f"Error: Exa: {clip(res.get('text', ''), 400)}"
        return f"## Exa: '{params.query}'\n\n{res.get('text', '').strip()}"

    # ─── RSS / ATOM ───────────────────────────────────────────────────────────

    class RssInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        url: str = Field(..., description="Feed URL (RSS, RDF or Atom)", min_length=4, max_length=2000)
        limit: int = Field(default=10, ge=1, le=50)

    @mcp.tool(name="rss_read", annotations={"readOnlyHint": True})
    async def rss_read(params: RssInput) -> str:
        """Read an RSS / Atom feed — blogs, news, podcasts, YouTube channel feeds.
        Returns the newest entries with dates, links and a summary."""
        url = params.url if "://" in params.url else f"https://{params.url}"
        try:
            final, body, _ctype = await fetch_screened(
                url, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return _handle_error(e, "Feed")
        title, entries = feed_entries(body, params.limit)
        if not entries:
            return (f"No feed entries at {final} — it is not RSS/Atom, or it is empty. "
                    "Try web_read on the page to find its feed link.")
        lines = [f"## {title or final}", ""]
        for e in entries:
            meta = " · ".join(x for x in (e["date"], e["author"]) if x)
            lines.append(f"- **{e['title'] or '(untitled)'}**" + (f" — {meta}" if meta else ""))
            if e["link"]:
                lines.append(f"  {e['link']}")
            if e["summary"]:
                lines.append(f"  {e['summary']}")
        return NL.join(lines)

    # ─── V2EX ─────────────────────────────────────────────────────────────────

    class V2exTopicsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        feed: Literal["hot", "latest", "node"] = Field(default="hot")
        node: str = Field(default="", description="Node name for feed=node, e.g. python, programmer, jobs",
                          max_length=40, pattern=r"^[A-Za-z0-9_-]*$")
        limit: int = Field(default=10, ge=1, le=40)

    @mcp.tool(name="v2ex_topics", annotations={"readOnlyHint": True})
    async def v2ex_topics(params: V2exTopicsInput) -> str:
        """V2EX (Chinese developer community): hot, latest, or one node's topics."""
        if params.feed == "node" and not params.node:
            return "Error: feed=node needs `node` (e.g. python, programmer, jobs)."
        try:
            if params.feed == "node":
                data = await _get_json(f"{V2EX}/topics/show.json", {"node_name": params.node})
            else:
                data = await _get_json(f"{V2EX}/topics/{params.feed}.json")
        except Exception as e:
            return _handle_error(e, "V2EX")
        if not isinstance(data, list) or not data:
            return "No V2EX topics."
        lines = [f"## V2EX — {params.node if params.feed == 'node' else params.feed}", ""]
        for t in data[: params.limit]:
            lines.append(f"- **{t.get('title', '')}** · {(t.get('node') or {}).get('title', '')} · "
                         f"{_fmt_count(t.get('replies'))} replies · {_date(t.get('created'))}")
            lines.append(f"  {t.get('url', '')}")
        return NL.join(lines)

    class V2exTopicInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        topic: str = Field(..., description="Topic id or v2ex.com/t/… URL", max_length=200)
        replies: int = Field(default=20, ge=0, le=100)

    @mcp.tool(name="v2ex_topic", annotations={"readOnlyHint": True})
    async def v2ex_topic(params: V2exTopicInput) -> str:
        """One V2EX topic: the post and its replies."""
        try:
            tid = parse_v2ex_topic(params.topic)
        except ValueError as e:
            return f"Error: {e}"
        try:
            topic, replies = await asyncio.gather(
                _get_json(f"{V2EX}/topics/show.json", {"id": tid}),
                _get_json(f"{V2EX}/replies/show.json", {"topic_id": tid}) if params.replies
                else asyncio.sleep(0, result=[]))
        except Exception as e:
            return _handle_error(e, "V2EX")
        if not topic:
            return f"No V2EX topic {tid}."
        t = topic[0]
        lines = [f"## {t.get('title', '')}",
                 f"{(t.get('member') or {}).get('username', '?')} · {(t.get('node') or {}).get('title', '')} · "
                 f"{_date(t.get('created'))} · {_fmt_count(t.get('replies'))} replies · {t.get('url', '')}", "",
                 clip(t.get("content") or "", 4000), ""]
        if replies:
            lines.append("### Replies")
            for i, r in enumerate(replies[: params.replies], 1):
                lines.append(f"{i}. **{(r.get('member') or {}).get('username', '?')}**: "
                             f"{clip((r.get('content') or '').replace(NL, ' '), 400)}")
        return NL.join(lines)

    # ─── BILIBILI ─────────────────────────────────────────────────────────────

    class BiliSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(default="", description="Search terms. Empty = what's popular now", max_length=200)
        order: Literal["totalrank", "click", "pubdate", "dm", "stow"] = Field(
            default="totalrank", description="relevance · most played · newest · most danmaku · most saved")
        limit: int = Field(default=10, ge=1, le=20)

    @mcp.tool(name="bilibili_search", annotations={"readOnlyHint": True})
    async def bilibili_search(params: BiliSearchInput) -> str:
        """Search Bilibili videos (or list what's popular when query is empty) —
        plays, danmaku, saves, duration, uploader."""
        try:
            if params.query:
                data = await _bili("/x/web-interface/search/type",
                                   {"search_type": "video", "keyword": params.query,
                                    "order": params.order, "page": 1})
                items = [{"title": _strip_html(v.get("title", ""), 200), "bvid": v.get("bvid"),
                          "up": v.get("author"), "views": v.get("play"), "likes": v.get("like"),
                          "saves": v.get("favorites"), "danmaku": v.get("danmaku"),
                          "dur": v.get("duration"), "date": _date(v.get("pubdate"))}
                         for v in data.get("result") or []]
            else:
                data = await _bili("/x/web-interface/popular", {"ps": params.limit, "pn": 1})
                items = [{"title": v.get("title"), "bvid": v.get("bvid"),
                          "up": (v.get("owner") or {}).get("name"),
                          "views": (v.get("stat") or {}).get("view"),
                          "likes": (v.get("stat") or {}).get("like"),
                          "saves": (v.get("stat") or {}).get("favorite"),
                          "danmaku": (v.get("stat") or {}).get("danmaku"),
                          "dur": _dur(v.get("duration")), "date": _date(v.get("pubdate"))}
                         for v in data.get("list") or []]
        except Exception as e:
            return f"Error: {e}" if isinstance(e, RuntimeError) else _handle_error(e, "Bilibili")
        if not items:
            return f"No Bilibili videos for '{params.query}'." if params.query else "Nothing popular returned."
        lines = [f"## Bilibili — " + (f"'{params.query}'" if params.query else "popular now"), ""]
        for v in items[: params.limit]:
            lines.append(f"- **{v['title']}** · {v['up']} · {v['dur']} · {v['date']}")
            lines.append(f"  ▶ {_fmt_count(v['views'])} · ♥ {_fmt_count(v['likes'])} · "
                         f"★ {_fmt_count(v['saves'])} · 弹幕 {_fmt_count(v['danmaku'])}")
            lines.append(f"  https://www.bilibili.com/video/{v['bvid']}")
        return NL.join(lines)

    class BiliVideoInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        video: str = Field(..., description="BV id or bilibili.com/video/… URL", max_length=300)
        comments: int = Field(default=5, ge=0, le=20, description="Top comments to include")

    @mcp.tool(name="bilibili_video", annotations={"readOnlyHint": True})
    async def bilibili_video(params: BiliVideoInput) -> str:
        """One Bilibili video: title, uploader, full stats, description, top comments."""
        try:
            bvid = parse_bvid(params.video)
        except ValueError as e:
            return f"Error: {e}"
        try:
            d = await _bili("/x/web-interface/wbi/view", {"bvid": bvid})
            replies = []
            if params.comments and d.get("aid"):
                rd = await _bili("/x/v2/reply/main", {"oid": d["aid"], "type": 1, "mode": 3})
                replies = (rd.get("replies") or [])[: params.comments]
        except Exception as e:
            return f"Error: {e}" if isinstance(e, RuntimeError) else _handle_error(e, "Bilibili")
        s = d.get("stat") or {}
        meta = [(d.get("owner") or {}).get("name", "?"), _dur(d.get("duration")), _date(d.get("pubdate")),
                d.get("tname") or "", f"https://www.bilibili.com/video/{bvid}"]
        lines = [f"## {d.get('title', bvid)}", " · ".join(x for x in meta if x), "",
                 f"▶ {_fmt_count(s.get('view'))} views · ♥ {_fmt_count(s.get('like'))} · "
                 f"coins {_fmt_count(s.get('coin'))} · ★ {_fmt_count(s.get('favorite'))} · "
                 f"shares {_fmt_count(s.get('share'))} · 💬 {_fmt_count(s.get('reply'))} · "
                 f"弹幕 {_fmt_count(s.get('danmaku'))}", "",
                 clip(d.get("desc") or "", 2000)]
        if replies:
            lines += ["", "### Top comments"]
            for r in replies:
                lines.append(f"- **{(r.get('member') or {}).get('uname', '?')}** (♥ {_fmt_count(r.get('like'))}): "
                             f"{clip(((r.get('content') or {}).get('message') or '').replace(NL, ' '), 300)}")
        return NL.join(lines)

    # ─── X / TWITTER ──────────────────────────────────────────────────────────

    class TwSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., min_length=1, max_length=300,
                           description="X search query — operators like from:, since:, min_faves: work")
        tab: Literal["Top", "Latest"] = Field(default="Top")
        limit: int = Field(default=10, ge=1, le=50)

    @mcp.tool(name="twitter_search", annotations={"readOnlyHint": True})
    async def twitter_search(params: TwSearchInput) -> str:
        """Search X/Twitter posts (needs your exported x.com cookies)."""
        try:
            data = await run_twitter(["search", "-t", params.tab, "-n", str(params.limit),
                                      "--json", "--", params.query])
        except TwitterError as e:
            return str(e) if str(e).startswith("X/Twitter is not") else f"Error: {e}"
        return format_tweets(data if isinstance(data, list) else [], f"X search: '{params.query}' ({params.tab})")

    class TwUserInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        user: str = Field(..., description="@handle or x.com profile URL", max_length=200)
        limit: int = Field(default=10, ge=0, le=50, description="Recent posts (0 = profile only)")

    @mcp.tool(name="twitter_user_posts", annotations={"readOnlyHint": True})
    async def twitter_user_posts(params: TwUserInput) -> str:
        """An X/Twitter account's profile (followers, bio) and its recent posts."""
        try:
            handle = parse_handle(params.user)
        except ValueError as e:
            return f"Error: {e}"
        try:
            prof = await run_twitter(["user", "--json", "--", handle])
            posts = (await run_twitter(["user-posts", "-n", str(params.limit), "--json", "--", handle])
                     if params.limit else [])
        except TwitterError as e:
            return str(e) if str(e).startswith("X/Twitter is not") else f"Error: {e}"
        p = prof if isinstance(prof, dict) else {}
        head = [f"## @{p.get('screenName', handle)} — {p.get('name', '')}",
                f"{_fmt_count(p.get('followers'))} followers · {_fmt_count(p.get('following'))} following · "
                f"{_fmt_count(p.get('tweets'))} posts · joined {str(p.get('createdAt') or '')[:16]}",
                clip(p.get("bio") or "", 400)]
        if p.get("url"):
            head.append(p["url"])
        if not params.limit:
            return NL.join(head)
        return NL.join(head) + "\n\n" + format_tweets(posts if isinstance(posts, list) else [], "Recent posts")

    class TwTweetInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        tweet: str = Field(..., description="Tweet id or x.com/…/status/… URL", max_length=300)
        replies: int = Field(default=10, ge=0, le=50)

    @mcp.tool(name="twitter_tweet", annotations={"readOnlyHint": True})
    async def twitter_tweet(params: TwTweetInput) -> str:
        """One X/Twitter post (including long-form Articles) and its replies."""
        try:
            tid = parse_tweet_id(params.tweet)
        except ValueError as e:
            return f"Error: {e}"
        try:
            data = await run_twitter(["tweet", "-n", str(max(params.replies, 1)), "--json", "--", tid])
        except TwitterError as e:
            return str(e) if str(e).startswith("X/Twitter is not") else f"Error: {e}"
        tweets = data if isinstance(data, list) else []
        if not tweets:
            return f"Tweet {tid} returned nothing (deleted, protected, or not visible to this account)."
        out = format_tweets(tweets[:1], "Post")
        if params.replies and len(tweets) > 1:
            out += "\n\n" + format_tweets(tweets[1: params.replies + 1], f"Replies ({len(tweets) - 1})")
        return out

    # ─── DOCTOR ───────────────────────────────────────────────────────────────

    class DoctorInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        probe: bool = Field(default=False, description="Also check the keyless channels live (~5 s)")

    @mcp.tool(name="reach_doctor", annotations={"readOnlyHint": True})
    async def reach_doctor(params: DoctorInput) -> str:
        """Which internet platforms Plutus can reach right now (the Agent Reach
        channel map): the tools and backend for each, and what is missing."""
        rows = doctor_rows()
        probes = await doctor_probes() if params.probe else {}
        mark = {"ready": "✅", "setup": "⚙️", "unavailable": "—"}
        lines = ["## Internet reach — platform → tools → backend", "",
                 "| | Platform | Tools | Backend | Notes |", "|---|---|---|---|---|"]
        for r in rows:
            note = r["note"]
            if r["platform"] in probes:
                note = (f"live: {probes[r['platform']]}" + (f" · {note}" if note else ""))
            lines.append(f"| {mark[r['status']]} | {r['platform']} | {r['tools']} | {r['backend']} | "
                         f"{note.replace('|', '/')} |")
        lines += ["", "✅ ready · ⚙️ needs setup (see Notes) · — not available on a server"]
        return NL.join(lines)
