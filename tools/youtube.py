"""
tools/youtube.py — research YouTube: read and "watch" videos, crawl channels,
follow competitors over time, and find what people search for.

Two ways in, used together:

- **The public pages** (``core/youtube_scrape.py``, yt-dlp): no key, no quota.
  The only source for transcripts of other people's videos, the "most replayed"
  heatmap, and YouTube's own search ranking.
- **The Data API** (``YOUTUBE_API_KEY``, optional): exact counts and publish
  dates for whole lists at ~1 quota unit per 50 videos. When the key is set it
  refines what the pages found; when it is missing or out of quota the tools say
  so and carry on with the page numbers rather than failing.

Search goes through the results page, not ``search.list``: the page is the
ranking a viewer actually sees, and ``search.list`` costs 100 units a call —
a hundred searches would spend a day's quota.

The API key travels in the ``X-Goog-Api-Key`` header, never the URL, so it
cannot surface in an exception message or a log line.

Owner-only numbers (watch time, retention, search terms, impressions/CTR, Search
Console) are in ``tools/youtube_studio.py``.
"""
import asyncio
import functools
import re
import statistics
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import anyio
import httpx
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT
from core import youtube_scrape as ys
from core import youtube_store as store

_ROOT = Path(__file__).resolve().parents[1]
API = "https://www.googleapis.com/youtube/v3"
_NOT_CONFIGURED = (
    "YouTube trending needs **YOUTUBE_API_KEY** — not configured. Create an API key "
    "in Google Cloud Console, enable **YouTube Data API v3**, and add it on the "
    "YouTube card. (Every other YouTube tool works without it.)"
)
_KEY_HINT = "add YOUTUBE_API_KEY for exact counts and publish dates"


# ── formatting ───────────────────────────────────────────────────────────────

def _fmt_count(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v or "—")
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "")
    return str(n)


def _n(v) -> str:
    """Exact, with separators — research tables need the real number."""
    return f"{int(v):,}" if isinstance(v, (int, float)) else "—"


_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def _iso_duration(s: str) -> int | None:
    m = _DUR.fullmatch(s or "")
    if not m or not s:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _age_days(published: str) -> int | None:
    try:
        d = date.fromisoformat((published or "")[:10])
    except ValueError:
        return None
    return max((datetime.now(timezone.utc).date() - d).days, 0)


def _per_day(views, published: str) -> float | None:
    days = _age_days(published)
    if views is None or days is None:
        return None
    return views / max(days, 1)


def _length(sec) -> str:
    return ys.fmt_ts(sec) if isinstance(sec, (int, float)) and sec else "—"


# ── Data API ─────────────────────────────────────────────────────────────────

class ApiError(RuntimeError):
    def __init__(self, status: int, reason: str, message: str):
        super().__init__(f"{reason or status}: {message}")
        self.status, self.reason = status, reason


def api_error(r: httpx.Response) -> ApiError:
    """Google's own explanation (disabled API, quota, bad key) rather than a bare 403."""
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    reasons = [e.get("reason", "") for e in err.get("errors") or []]
    return ApiError(r.status_code, reasons[0] if reasons else "",
                    (err.get("message") or r.text[:200] or f"HTTP {r.status_code}").strip())


async def _api(path: str, params: dict, *, token: str = "") -> dict:
    headers = {"User-Agent": "PlutusMCP/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["X-Goog-Api-Key"] = cfg.youtube_api_key
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(f"{API}/{path}", params=params, headers=headers)
    if r.status_code >= 400:
        raise api_error(r)
    return r.json()


async def video_stats(ids: list[str], *, token: str = "") -> dict[str, dict]:
    """Exact numbers for up to any count of videos, 50 per call (1 unit each)."""
    out: dict[str, dict] = {}
    ids = [i for i in dict.fromkeys(ids) if i]
    for i in range(0, len(ids), 50):
        data = await _api("videos", {"part": "snippet,statistics,contentDetails",
                                     "id": ",".join(ids[i:i + 50]), "maxResults": 50}, token=token)
        for it in data.get("items") or []:
            sn, st, cd = it.get("snippet", {}), it.get("statistics", {}), it.get("contentDetails", {})

            def num(k):
                return int(st[k]) if st.get(k) is not None else None
            out[it["id"]] = {
                "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                "channel_id": sn.get("channelId", ""),
                "published": (sn.get("publishedAt") or "")[:10],
                "views": num("viewCount"), "likes": num("likeCount"), "comments": num("commentCount"),
                "duration": _iso_duration(cd.get("duration", "")),
                "tags": sn.get("tags") or [], "category_id": sn.get("categoryId", ""),
                "description": sn.get("description", ""),
                "definition": cd.get("definition", ""), "captions": cd.get("caption") == "true",
            }
    return out


async def _exact(ids: list[str]) -> tuple[dict[str, dict], str]:
    """(stats by id, note). Never raises: without the API the page numbers stand."""
    if not cfg.youtube_api_key or not ids:
        return {}, ""
    try:
        return await video_stats(ids), ""
    except Exception as e:
        return {}, f"Data API unavailable ({str(e)[:160]}) — showing the public page's numbers."


def channel_params(ref: str) -> dict:
    """channels.list selector for an @handle, UC… id or channel URL."""
    ref = (ref or "").strip()
    if ref.startswith(("http://", "https://")):
        seg = urlparse(ref).path.strip("/").split("/")
        if seg and seg[0].startswith("@"):
            return {"forHandle": seg[0]}
        if len(seg) > 1 and seg[0] == "channel":
            return {"id": seg[1]}
        if len(seg) > 1 and seg[0] == "user":
            return {"forUsername": seg[1]}
        raise ValueError(f"cannot read a channel from {ref!r} — use its @handle")
    if ys.is_channel_id(ref):
        return {"id": ref}
    return {"forHandle": "@" + ref.lstrip("@")}


async def _thread(fn, *args, **kw):
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kw))


# ── collection shared by channel_videos and the tracker ─────────────────────

async def collect_channel(ref: str, *, max_videos: int = 30, max_shorts: int = 15) -> dict:
    """A channel's totals and recent uploads, exact when the Data API allows."""
    lv = await _thread(ys.channel_listing, ref, "videos", max_videos)
    uploads = [{**e, "kind": "video"} for e in lv["entries"]]
    if max_shorts:
        try:
            ls = await _thread(ys.channel_listing, ref, "shorts", max_shorts)
            uploads += [{**e, "kind": "short"} for e in ls["entries"]]
        except ys.ScrapeError:
            pass          # no Shorts tab is a channel without Shorts, not an error
    out = {"channel_id": lv["channel_id"], "title": lv["channel"], "handle": lv["handle"],
           "subscribers": lv["subscribers"], "views": None, "videos": None,
           "source": "page", "uploads": uploads, "note": ""}
    if not cfg.youtube_api_key or not out["channel_id"]:
        return out
    try:
        ch = await _api("channels", {"part": "statistics", "id": out["channel_id"]})
        st = (ch.get("items") or [{}])[0].get("statistics", {})
        stats = await video_stats([u["id"] for u in uploads])
    except Exception as e:
        out["note"] = f"Data API unavailable ({str(e)[:160]}) — page numbers (rounded) stored."
        return out
    for k, sk in (("subscribers", "subscriberCount"), ("views", "viewCount"), ("videos", "videoCount")):
        if st.get(sk) is not None:
            out[k] = int(st[sk])
    for u in uploads:
        s = stats.get(u["id"])
        if s:
            u.update(views=s["views"], likes=s["likes"], comments=s["comments"],
                     published=s["published"], duration=s["duration"])
    out["source"] = "api"
    return out


def register_youtube_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    # ── search ───────────────────────────────────────────────────────────────
    class SearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search terms", min_length=1, max_length=300)
        max_results: int = Field(default=10, description="Max results (1–50)", ge=1, le=50)
        order: Literal["relevance", "date"] = Field(default="relevance", description="relevance = YouTube's ranking; date = newest first")

    @mcp.tool(name="youtube_search", annotations={"readOnlyHint": True})
    async def youtube_search(params: SearchInput) -> str:
        """Search YouTube the way a viewer sees it: the ranked results for a query with
        views, publish date, views/day, length and channel. No quota used."""
        try:
            items = await _thread(ys.search, params.query, params.max_results, params.order)
        except (ys.ScrapeError, ValueError) as e:
            return f"Error: YouTube search failed: {e}"
        if not items:
            return f"## YouTube search: '{params.query}'\n\nNo results."
        stats, note = await _exact([i["id"] for i in items])
        lines = [f"## YouTube search: '{params.query}' ({params.order})\n"]
        for n, it in enumerate(items, 1):
            s = stats.get(it["id"], {})
            views = s.get("views", it["views"])
            pub = s.get("published", "")
            pd = _per_day(views, pub)
            bits = [f"{_n(views)} views" if views is not None else "views —"]
            if pub:
                bits.append(f"{pub} ({_age_days(pub)} d, {_n(pd)}/day)")
            bits.append(_length(s.get("duration") or it["duration"]))
            lines.append(f"{n}. **{it['title']}** — {it['channel']} · " + " · ".join(bits)
                         + f"\n   https://youtu.be/{it['id']}")
        if not stats and not note:
            lines.append(f"\n_View counts from the results page (rounded above 1,000); {_KEY_HINT}._")
        if note:
            lines.append(f"\n_{note}_")
        return "\n".join(lines)

    # ── channel ──────────────────────────────────────────────────────────────
    class ChannelInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        channel: str = Field(..., description="Channel @handle, channel ID (UC…), or channel URL", min_length=1, max_length=300)

    @mcp.tool(name="youtube_channel", annotations={"readOnlyHint": True})
    async def youtube_channel(params: ChannelInput) -> str:
        """Channel profile — subscribers, total views, video count, created date,
        country, channel keywords and About text — by @handle, ID or URL."""
        c = params.channel.strip()
        if cfg.youtube_api_key:
            try:
                sel = channel_params(c)
                data = await _api("channels", {"part": "snippet,statistics,brandingSettings", **sel})
                items = data.get("items") or []
                if not items:
                    return f"No channel found for '{c}'."
                it = items[0]
                sn, st = it.get("snippet", {}), it.get("statistics", {})
                kw = (it.get("brandingSettings", {}).get("channel", {}) or {}).get("keywords", "")
                desc = (sn.get("description") or "").strip()
                return (
                    f"## {sn.get('title', '')}\n\n"
                    f"**Handle:** {sn.get('customUrl', '')}\n"
                    f"**Subscribers:** {_n(int(st['subscriberCount'])) if st.get('subscriberCount') else 'hidden'}\n"
                    f"**Total views:** {_n(int(st.get('viewCount', 0)))}\n"
                    f"**Videos:** {_n(int(st.get('videoCount', 0)))}\n"
                    f"**Created:** {(sn.get('publishedAt') or '')[:10]}\n"
                    f"**Country:** {sn.get('country', '—')}\n"
                    f"**Channel ID:** {it.get('id', '')}\n"
                    f"**Channel keywords:** {kw or '—'}\n"
                    f"**About:** {desc[:800]}"
                )
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                note = f"Data API unavailable ({str(e)[:160]}) — read from the public page instead."
        else:
            note = f"From the public channel page; {_KEY_HINT}."
        try:
            ch = await _thread(ys.channel_listing, c, "videos", 1)
        except (ys.ScrapeError, ValueError) as e:
            return f"Error: could not read channel '{c}': {e}"
        return (
            f"## {ch['channel']}\n\n"
            f"**Handle:** {ch['handle']}\n"
            f"**Subscribers:** {_n(ch['subscribers'])} (rounded by YouTube)\n"
            f"**Channel ID:** {ch['channel_id']}\n"
            f"**Channel keywords:** {', '.join(ch['tags']) or '—'}\n"
            f"**About:** {ch['description'][:800]}\n\n_{note}_"
        )

    # ── a channel's uploads ──────────────────────────────────────────────────
    class ChannelVideosInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        channel: str = Field(..., description="Channel @handle, channel ID (UC…), or channel URL", min_length=1, max_length=300)
        tab: Literal["videos", "shorts", "streams"] = Field(default="videos", description="Which tab to crawl")
        max_results: int = Field(default=30, description="How many of the newest uploads (1–200)", ge=1, le=200)
        sort: Literal["recent", "views", "views_per_day"] = Field(default="recent", description="Order of the table")

    @mcp.tool(name="youtube_channel_videos", annotations={"readOnlyHint": True})
    async def youtube_channel_videos(params: ChannelVideosInput) -> str:
        """Crawl a channel's uploads (videos, Shorts or streams) into a stats table:
        views, views/day, × the list's median, likes, comments, engagement, publish
        date and length. For finding what works on any channel, including your own."""
        try:
            lst = await _thread(ys.channel_listing, params.channel, params.tab, params.max_results)
        except (ys.ScrapeError, ValueError) as e:
            return f"Error: could not read {params.tab} of '{params.channel}': {e}"
        rows = lst["entries"]
        if not rows:
            return f"## {lst['channel'] or params.channel} — {params.tab}\n\nNo uploads on this tab."
        stats, note = await _exact([r["id"] for r in rows])
        for r in rows:
            s = stats.get(r["id"])
            if s:
                r.update(views=s["views"], likes=s["likes"], comments=s["comments"],
                         published=s["published"], duration=s["duration"] or r["duration"])
            r["per_day"] = _per_day(r.get("views"), r.get("published", ""))
            v, lk, cm = r.get("views"), r.get("likes"), r.get("comments")
            r["engagement"] = ((lk or 0) + (cm or 0)) / v * 100 if v and lk is not None else None
        known = [r["views"] for r in rows if isinstance(r.get("views"), (int, float))]
        med = statistics.median(known) if known else None
        if params.sort == "views":
            rows.sort(key=lambda r: r.get("views") or -1, reverse=True)
        elif params.sort == "views_per_day":
            rows.sort(key=lambda r: r.get("per_day") or -1, reverse=True)

        exact = bool(stats)
        head = [f"## {lst['channel']} ({lst['handle']}) — {params.tab}, newest {len(rows)}",
                f"Subscribers: {_n(lst['subscribers'])} · Median views: {_n(med)} · "
                f"Mean views: {_n(statistics.mean(known)) if known else '—'} · "
                f"Total views (these {len(known)}): {_n(sum(known)) if known else '—'}",
                "Source: " + ("Data API (exact)" if exact else "public channel page (views rounded above 1,000, no dates)"),
                "_× med = views ÷ this list's median views. Eng % = (likes + comments) ÷ views._\n"]
        if exact:
            head.append("| # | Title | Views | /day | × med | Likes | Comments | Eng % | Published | Length | ID |")
            head.append("|---|---|---|---|---|---|---|---|---|---|---|")
        else:
            head.append("| # | Title | Views | × med | Length | ID |")
            head.append("|---|---|---|---|---|---|")
        for n, r in enumerate(rows, 1):
            title = r["title"].replace("|", "/")
            mult = f"{r['views'] / med:.2f}" if med and isinstance(r.get("views"), (int, float)) else "—"
            if exact:
                eng = f"{r['engagement']:.1f}" if r.get("engagement") is not None else "—"
                head.append(f"| {n} | {title} | {_n(r.get('views'))} | {_n(r.get('per_day'))} | {mult} | "
                            f"{_n(r.get('likes'))} | {_n(r.get('comments'))} | {eng} | "
                            f"{r.get('published') or '—'} | {_length(r.get('duration'))} | {r['id']} |")
            else:
                head.append(f"| {n} | {title} | {_n(r.get('views'))} | {mult} | {_length(r.get('duration'))} | {r['id']} |")
        if note:
            head.append(f"\n_{note}_")
        elif not exact:
            head.append(f"\n_{_KEY_HINT}._")
        return "\n".join(head)

    # ── one video ────────────────────────────────────────────────────────────
    class VideoInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        video_id: str = Field(..., description="Video ID or any YouTube video URL", min_length=5, max_length=300)

    @mcp.tool(name="youtube_video", annotations={"readOnlyHint": True})
    async def youtube_video(params: VideoInput) -> str:
        """Quick video stats — views, likes, comments, length, publish date, tags,
        category. For the transcript, chapters and description use youtube_watch."""
        try:
            vid = ys.parse_video_id(params.video_id)
        except ValueError as e:
            return f"Error: {e}"
        stats, note = await _exact([vid])
        s = stats.get(vid)
        if not s:
            try:
                v = await _thread(ys.video_info, vid)
            except ys.ScrapeError as e:
                return f"No video found for '{vid}': {e}"
            s = {**v, "category_id": ", ".join(v["categories"])}
            note = note or "From the public watch page."
        return (
            f"## {s['title']}\n\n"
            f"**Channel:** {s['channel']}\n"
            f"**Published:** {s['published']} ({_age_days(s['published'])} days ago)\n"
            f"**Views:** {_n(s['views'])} ({_n(_per_day(s['views'], s['published']))}/day)\n"
            f"**Likes:** {_n(s['likes'])}\n"
            f"**Comments:** {_n(s['comments'])}\n"
            f"**Length:** {_length(s['duration'])}\n"
            f"**Tags:** {', '.join(s.get('tags') or []) or '—'}\n"
            f"**Category:** {s.get('category_id') or '—'}\n"
            f"**Link:** https://youtu.be/{vid}"
            + (f"\n\n_{note}_" if note else "")
        )

    # ── watch: everything a video says, in one read ──────────────────────────
    class WatchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        video: str = Field(..., description="Video ID or any YouTube video URL", min_length=5, max_length=300)
        transcript: bool = Field(default=True, description="Include the full transcript")
        comments: int = Field(default=0, description="Also include this many top comments (0–50)", ge=0, le=50)
        lang: str = Field(default="", description="Transcript language code (default: the language spoken)", max_length=12)
        max_chars: int = Field(default=40000, description="Cap on transcript length", ge=500, le=200000)

    @mcp.tool(name="youtube_watch", annotations={"readOnlyHint": True})
    async def youtube_watch(params: WatchInput) -> str:
        """"Watch" a YouTube video as text: title, stats, full description, tags,
        chapters, the most-replayed moments, the complete timestamped transcript and
        optionally top comments. Works on any public video, no key needed."""
        try:
            vid = ys.parse_video_id(params.video)
            v = await _thread(ys.video_info, vid)
        except (ys.ScrapeError, ValueError) as e:
            return f"Error: could not read that video: {e}"
        out = [f"# {v['title']}",
               f"**Channel:** {v['channel']} ({v['handle']}) · {_n(v['subscribers'])} subscribers",
               f"**Published:** {v['published']} · **Length:** {_length(v['duration'])} · "
               f"**Views:** {_n(v['views'])} · **Likes:** {_n(v['likes'])} · **Comments:** {_n(v['comments'])}",
               f"**Category:** {', '.join(v['categories']) or '—'} · **Language:** {v['language'] or '—'}",
               f"**Tags:** {', '.join(v['tags']) or '—'}",
               f"**Thumbnail:** {v['thumbnail']}",
               f"**Link:** https://youtu.be/{vid}",
               "", "## Description", v["description"][:6000] or "(none)"]
        if v["chapters"]:
            out += ["", "## Chapters"] + [f"- {ys.fmt_ts(c['start'])} {c['title']}" for c in v["chapters"]]
        peaks = ys.most_replayed(v["heatmap"])
        if peaks:
            out += ["", "## Most replayed (YouTube's public heatmap; 1.00 = the most replayed point, opening excluded)"]
            out += [f"- {ys.fmt_ts(p['start'])}–{ys.fmt_ts(p['end'])} · {p['value']:.2f}" for p in peaks]
        if params.transcript:
            try:
                t = await _thread(ys.fetch_transcript, v, params.lang)
                text = ys.format_transcript(t["segments"], v["chapters"])
                kind = "creator's subtitles" if t["kind"] == "manual" else "YouTube auto-captions"
                kind += " (machine-translated)" if t["translated"] else ""
                out += ["", f"## Transcript — {t['lang']}, {kind}"]
                if len(text) > params.max_chars:
                    out.append(text[:params.max_chars]
                               + f"\n\n[… truncated — {len(text) - params.max_chars:,} more characters; raise max_chars]")
                else:
                    out.append(text)
            except ys.ScrapeError as e:
                out += ["", f"## Transcript\nNot available: {e}"]
        if params.comments:
            out += ["", "## Top comments", await _comments_text(vid, params.comments, "relevance")]
        return "\n".join(out)

    class TranscriptInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        video: str = Field(..., description="Video ID or any YouTube video URL", min_length=5, max_length=300)
        lang: str = Field(default="", description="Language code (default: the language spoken)", max_length=12)
        timestamps: bool = Field(default=True, description="Prefix each ~30 s paragraph with its timestamp")
        max_chars: int = Field(default=60000, description="Cap on length", ge=500, le=200000)

    @mcp.tool(name="youtube_transcript", annotations={"readOnlyHint": True})
    async def youtube_transcript(params: TranscriptInput) -> str:
        """Just the transcript of a YouTube video (creator subtitles, else auto
        captions), grouped by chapter, with timestamps. No key needed."""
        try:
            vid = ys.parse_video_id(params.video)
            v = await _thread(ys.video_info, vid)
            t = await _thread(ys.fetch_transcript, v, params.lang)
        except (ys.ScrapeError, ValueError) as e:
            return f"Error: no transcript: {e}"
        text = ys.format_transcript(t["segments"], v["chapters"], timestamps=params.timestamps)
        kind = "creator's subtitles" if t["kind"] == "manual" else "auto-captions"
        head = f"## Transcript: {v['title']} — {v['channel']}\n_{t['lang']}, {kind}" \
               + (", machine-translated" if t["translated"] else "") + f" · https://youtu.be/{vid}_\n"
        if len(text) > params.max_chars:
            text = text[:params.max_chars] + f"\n\n[… truncated — {len(text) - params.max_chars:,} more characters]"
        return head + "\n" + text

    # ── comments ─────────────────────────────────────────────────────────────
    async def _comments_text(vid: str, n: int, order: str) -> str:
        rows, note = [], ""
        if cfg.youtube_api_key:
            try:
                data = await _api("commentThreads", {
                    "part": "snippet", "videoId": vid, "maxResults": min(n, 100),
                    "order": "time" if order == "time" else "relevance", "textFormat": "plainText"})
                for it in data.get("items") or []:
                    top = it["snippet"]["topLevelComment"]["snippet"]
                    rows.append({"author": top.get("authorDisplayName", ""), "likes": top.get("likeCount", 0),
                                 "replies": it["snippet"].get("totalReplyCount", 0),
                                 "text": top.get("textDisplay", ""),
                                 "published": (top.get("publishedAt") or "")[:10], "pinned": False})
            except ApiError as e:
                if e.reason == "commentsDisabled":
                    return "Comments are turned off on this video."
                note = f"Data API unavailable ({str(e)[:120]}) — read from the page."
            except Exception as e:
                note = f"Data API unavailable ({str(e)[:120]}) — read from the page."
        if not rows:
            try:
                rows = await _thread(ys.comments, vid, n, "time" if order == "time" else "top")
            except ys.ScrapeError as e:
                return f"Could not read comments: {e}"
            for r in rows:
                if isinstance(r["published"], (int, float)):
                    r["published"] = time.strftime("%Y-%m-%d", time.gmtime(r["published"]))
        if not rows:
            return "No comments."
        lines = []
        for r in rows[:n]:
            meta = [f"{_n(r['likes'])} likes"]
            if r.get("replies") is not None:
                meta.append(f"{r['replies']} replies")
            if r.get("published"):
                meta.append(str(r["published"]))
            if r.get("pinned"):
                meta.append("pinned")
            text = " ".join(r["text"].split())
            lines.append(f"- **{r['author']}** · {' · '.join(meta)}\n  {text[:600]}")
        return "\n".join(lines) + (f"\n\n_{note}_" if note else "")

    class CommentsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        video: str = Field(..., description="Video ID or any YouTube video URL", min_length=5, max_length=300)
        max_results: int = Field(default=20, description="How many top-level comments (1–100)", ge=1, le=100)
        order: Literal["relevance", "time"] = Field(default="relevance", description="relevance = top comments; time = newest")

    @mcp.tool(name="youtube_comments", annotations={"readOnlyHint": True})
    async def youtube_comments(params: CommentsInput) -> str:
        """Read a video's comments — top or newest — with likes and reply counts.
        What viewers ask for, complain about and quote back."""
        try:
            vid = ys.parse_video_id(params.video)
        except ValueError as e:
            return f"Error: {e}"
        return f"## Comments on https://youtu.be/{vid} ({params.order})\n\n" + \
            await _comments_text(vid, params.max_results, params.order)

    # ── keyword research ─────────────────────────────────────────────────────
    class KeywordsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Seed keyword", min_length=1, max_length=150)
        source: Literal["youtube", "google", "both"] = Field(default="youtube", description="Whose autocomplete to read")
        expand: bool = Field(default=False, description="Also read '<seed> a' … '<seed> z' (27 lookups) for the long tail")
        lang: str = Field(default="en", description="Interface language (hl)", max_length=8)
        region: str = Field(default="US", description="Country (gl)", min_length=2, max_length=2)

    @mcp.tool(name="youtube_keywords", annotations={"readOnlyHint": True})
    async def youtube_keywords(params: KeywordsInput) -> str:
        """Keyword research from YouTube's (and Google's) autocomplete — the phrases
        people actually type, in the order YouTube suggests them. No key needed."""
        seeds = [params.query] + ([f"{params.query} {c}" for c in "abcdefghijklmnopqrstuvwxyz"]
                                  if params.expand else [])
        sources = ["youtube", "google"] if params.source == "both" else [params.source]
        sem = asyncio.Semaphore(8)

        async def one(client, q, src):
            p = {"q": q, "hl": params.lang, "gl": params.region.upper()}
            if src == "youtube":
                url, p = ys.YT_SUGGEST_URL, {**p, "client": "youtube", "ds": "yt"}
            else:
                url, p = ys.SUGGEST_URL, {**p, "client": "firefox"}
            async with sem:
                try:
                    r = await client.get(url, params=p)
                    r.raise_for_status()
                    return ys.parse_suggestions(r.text if src == "youtube" else r.json())
                except Exception:
                    return None
        out = [f"## Autocomplete for '{params.query}' ({params.lang}-{params.region.upper()})"]
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            for src in sources:
                results = await asyncio.gather(*(one(client, q, src) for q in seeds))
                if all(r is None for r in results):
                    out.append(f"\n### {src.title()}\nError: the autocomplete service did not answer.")
                    continue
                seen = list(dict.fromkeys(s for r in results if r for s in r))
                out.append(f"\n### {src.title()} — {len(seen)} suggestions")
                out += [f"- {s}" for s in seen] or ["(none)"]
        return "\n".join(out)

    # ── trending (the one tool that needs the key) ───────────────────────────
    class TrendingInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        region: str = Field(default="US", description="ISO 3166-1 region code (US, DE, GB…)", min_length=2, max_length=2)
        max_results: int = Field(default=10, description="Max results (1–50)", ge=1, le=50)
        category_id: str = Field(default="", description="Video category id, e.g. 28 = Science & Technology", max_length=4)

    @mcp.tool(name="youtube_trending", annotations={"readOnlyHint": True})
    async def youtube_trending(params: TrendingInput) -> str:
        """Most-popular videos right now for a region (optionally one category)."""
        if not cfg.youtube_api_key:
            return _NOT_CONFIGURED
        try:
            q = {"part": "snippet,statistics", "chart": "mostPopular",
                 "regionCode": params.region.upper(), "maxResults": params.max_results}
            if params.category_id:
                q["videoCategoryId"] = params.category_id
            data = await _api("videos", q)
        except Exception as e:
            return f"Error: YouTube trending: {e}"
        items = data.get("items") or []
        if not items:
            return f"No trending videos for region '{params.region.upper()}'."
        lines = [f"## Trending on YouTube — {params.region.upper()}\n"]
        for it in items:
            sn, st = it.get("snippet", {}), it.get("statistics", {})
            lines.append(f"- **{sn.get('title', '')}** — {sn.get('channelTitle', '')} "
                         f"({_fmt_count(st.get('viewCount'))} views)\n  https://youtu.be/{it.get('id', '')}")
        return "\n".join(lines)

    # ── Gemini actually watches it ───────────────────────────────────────────
    class AskInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        video: str = Field(..., description="Public video ID or URL", min_length=5, max_length=300)
        question: str = Field(default="", description="What to look for. Default: a section-by-section account of what is shown and heard", max_length=4000)

    @mcp.tool(name="youtube_ask_video", annotations={"readOnlyHint": True})
    async def youtube_ask_video(params: AskInput) -> str:
        """Have Gemini watch the actual video (frames and audio) and answer a question —
        what is on screen, the edit, the hook, text overlays. Needs a Gemini key in
        Settings → AI providers. Public videos only."""
        from core import ai_providers as ap
        try:
            vid = ys.parse_video_id(params.video)
        except ValueError as e:
            return f"Error: {e}"
        acct = next((a for a in ap.load_accounts(_ROOT).get("gemini", [])
                     if ap.stored_token(_ROOT, "gemini", a.get("id", ""))), None)
        if not acct:
            return ("This needs a Gemini API key — add one under Settings → AI providers → "
                    "Gemini (free key from https://aistudio.google.com/apikey).")
        key = ap.stored_token(_ROOT, "gemini", acct["id"])
        model = await _thread(ap.resolve_model, _ROOT, "gemini", acct["id"], "")
        question = params.question or (
            "Watch this video and describe it section by section with timestamps: what is "
            "shown on screen, what is said, text overlays, the editing and pacing, how the "
            "first 15 seconds hook the viewer, and any call to action.")
        url = f"https://www.youtube.com/watch?v={vid}"
        payload = {"contents": [{"role": "user", "parts": [{"fileData": {"fileUri": url}},
                                                           {"text": question}]}]}
        base = ap.PROVIDERS["gemini"]["api_base"]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                r = await client.post(f"{base}/models/{model}:generateContent", json=payload,
                                      headers={"x-goog-api-key": key})
            body = r.json() if r.content else {}
        except Exception as e:
            return f"Error: Gemini did not answer: {type(e).__name__}: {str(e)[:200]}"
        if r.status_code >= 400:
            msg = (body.get("error") or {}).get("message") or f"HTTP {r.status_code}"
            return f"Error: Gemini refused the video: {msg}"
        parts = ((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
        if not text:
            reason = (body.get("candidates") or [{}])[0].get("finishReason") or \
                (body.get("promptFeedback") or {}).get("blockReason") or "empty answer"
            return f"Error: Gemini returned no text ({reason})."
        return f"## Gemini watched {url}\n_Model: {model} · account: {acct.get('label', acct['id'])}_\n\n{text}"

    # ── tracking channels over time ──────────────────────────────────────────
    class TrackInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        action: Literal["add", "remove", "list", "snapshot"] = Field(..., description="add/remove a channel, list tracked ones, or take a snapshot now")
        channel: str = Field(default="", description="@handle, UC… id or URL (add/remove; snapshot: one channel, empty = all)", max_length=300)
        note: str = Field(default="", description="Why it is tracked (add)", max_length=300)
        max_videos: int = Field(default=0, description="Recent long-form uploads captured per snapshot, plus up to 15 Shorts. 0 = the channel's own setting (30 unless set on add)", ge=0, le=100)

    @mcp.tool(name="youtube_track", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def youtube_track(params: TrackInput) -> str:
        """Track channels (yours or competitors') over time. `add` stores a channel and
        takes its first snapshot; `snapshot` records subscribers, total views and the
        views of recent uploads for every tracked channel — schedule it daily. Read
        the change with youtube_track_report."""
        act = params.action
        if act == "list":
            rows = store.list_channels(_ROOT)
            if not rows:
                return "No channels tracked yet — youtube_track(action='add', channel='@handle')."
            lines = ["## Tracked channels\n", "| Channel | Handle | Subscribers | Snapshots | Last snapshot | Note |",
                     "|---|---|---|---|---|---|"]
            for r in rows:
                last = time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["last_taken"])) if r["last_taken"] else "—"
                lines.append(f"| {r['title']} | {r['handle']} | {_n(r['subscribers'])} | {r['snapshots']} | "
                             f"{last} UTC | {r['note'] or ''} |")
            return "\n".join(lines)

        if act == "remove":
            ch = store.find_channel(_ROOT, params.channel)
            if not ch:
                return f"'{params.channel}' is not tracked."
            store.remove_channel(_ROOT, ch["channel_id"])
            return f"Stopped tracking {ch['title']}. Its snapshots are kept; adding it again resumes the history."

        async def snap(ref: str, size: int) -> str:
            data = await collect_channel(ref, max_videos=params.max_videos or size or 30)
            if not data["channel_id"]:
                raise ys.ScrapeError("could not resolve the channel id")
            ts = store.record_snapshot(_ROOT, data["channel_id"], subscribers=data["subscribers"],
                                       views=data["views"], videos=data["videos"],
                                       source=data["source"], uploads=data["uploads"])
            src = "Data API, exact" if data["source"] == "api" else "public page, rounded"
            return (f"- **{data['title']}** — {_n(data['subscribers'])} subscribers, "
                    f"{_n(data['views'])} total views, {len(data['uploads'])} uploads recorded "
                    f"({src}) at {time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts))} UTC"
                    + (f"\n  _{data['note']}_" if data["note"] else ""))

        if act == "add":
            if not params.channel:
                return "Error: name the channel to add."
            try:
                data = await collect_channel(params.channel, max_videos=params.max_videos or 30)
            except (ys.ScrapeError, ValueError) as e:
                return f"Error: could not read '{params.channel}': {e}"
            if not data["channel_id"]:
                return f"Error: could not resolve a channel id for '{params.channel}'."
            ch, created = store.add_channel(_ROOT, data["channel_id"], handle=data["handle"],
                                            title=data["title"], note=params.note,
                                            max_videos=params.max_videos)
            ts = store.record_snapshot(_ROOT, data["channel_id"], subscribers=data["subscribers"],
                                       views=data["views"], videos=data["videos"],
                                       source=data["source"], uploads=data["uploads"])
            verb = "Now tracking" if created else "Already tracked — refreshed"
            return (f"{verb} **{ch['title']}** ({ch['handle']}, {ch['channel_id']}).\n"
                    f"Snapshot stored at {time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts))} UTC: "
                    f"{_n(data['subscribers'])} subscribers, {len(data['uploads'])} uploads.\n\n"
                    "Growth needs more snapshots. Schedule one daily: Plutus → Schedules → kind "
                    "`tool`, tool `youtube_track`, params `{\"action\": \"snapshot\"}`, cron `0 6 * * *`. "
                    "Then youtube_track_report shows what changed."
                    + (f"\n_{data['note']}_" if data["note"] else ""))

        # snapshot
        if params.channel:
            ch = store.find_channel(_ROOT, params.channel)
            targets = [ch] if ch else []
            if not targets:
                return f"'{params.channel}' is not tracked — add it first."
        else:
            targets = store.list_channels(_ROOT)
            if not targets:
                return "No channels tracked yet — youtube_track(action='add', channel='@handle')."
        lines = [f"## Snapshot — {len(targets)} channel(s)\n"]
        for t in targets:
            try:
                lines.append(await snap(t["channel_id"], t.get("max_videos") or 30))
            except Exception as e:
                lines.append(f"- **{t['title']}** — failed: {str(e)[:200]}")
        return "\n".join(lines)

    class ReportInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        channel: str = Field(default="", description="One tracked channel (@handle, id or title); empty = all", max_length=300)
        days: int = Field(default=30, description="Window to compare, ending at the latest snapshot", ge=1, le=3650)
        top: int = Field(default=5, description="How many fastest-gaining videos to list", ge=1, le=50)

    @mcp.tool(name="youtube_track_report", annotations={"readOnlyHint": True})
    async def youtube_track_report(params: ReportInput) -> str:
        """What changed for tracked channels over a window: subscriber, view and upload
        deltas, new uploads, and the videos that gained the most views — from the
        snapshots youtube_track stored."""
        if params.channel:
            ch = store.find_channel(_ROOT, params.channel)
            targets = [ch] if ch else []
            if not targets:
                return f"'{params.channel}' is not tracked."
        else:
            targets = store.list_channels(_ROOT)
            if not targets:
                return "No channels tracked yet — youtube_track(action='add', channel='@handle')."
        since = int(time.time()) - params.days * 86400
        out = [f"# Tracker report — last {params.days} days"]

        def day(ts):
            return time.strftime("%Y-%m-%d", time.gmtime(ts))
        for t in targets:
            r = store.report(_ROOT, t["channel_id"], since, top=params.top)
            out.append(f"\n## {t['title']} ({t['handle']})")
            if not r.get("start"):
                out.append("No snapshots yet.")
                continue
            s, e = r["start"], r["end"]
            if s["taken_at"] == e["taken_at"]:
                out.append(f"Only one snapshot in reach ({day(e['taken_at'])}): "
                           f"{_n(e['subscribers'])} subscribers. Change needs a second one.")
                continue
            days = r["days"]
            out.append(f"Window: {day(s['taken_at'])} → {day(e['taken_at'])} "
                       f"({days:.1f} days, {r['snapshots']} snapshots stored in total)")
            for label, k in (("Subscribers", "subscribers"), ("Total views", "views"), ("Videos", "videos")):
                d = r.get(k)
                if s[k] is None and e[k] is None:
                    continue          # never measured (no API key) — nothing to report
                if d is None:
                    out.append(f"{label}: {_n(s[k])} → {_n(e[k])}")
                elif days < 1:
                    # A per-day rate over minutes extrapolates noise into a trend.
                    out.append(f"{label}: {_n(s[k])} → {_n(e[k])} ({d:+,})")
                else:
                    out.append(f"{label}: {_n(s[k])} → {_n(e[k])} ({d:+,}, {d / days:+,.1f}/day)")
            if r.get("mixed_sources"):
                out.append("_Note: this window mixes exact (Data API) and rounded (public page) readings — "
                           "small deltas may be rounding._")
            if r["new_uploads"]:
                out.append("\n**New uploads in the window:**")
                out += [f"- {u['published'] or 'first seen ' + day(u['first_seen'])} · {u['kind'] or 'video'} · "
                        f"{u['title']} · https://youtu.be/{u['video_id']}" for u in r["new_uploads"]]
            if r["top_gainers"]:
                out.append("\n**Most views gained in the window:**")
                out += [f"- +{_n(g['gained'])} · {g['title']} ({_n(g['views_start'])} → {_n(g['views_end'])}) · "
                        f"https://youtu.be/{g['video_id']}" for g in r["top_gainers"]]
        return "\n".join(out)
