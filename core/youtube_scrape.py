"""Keyless YouTube reading — what the Data API cannot give, or charges quota for.

The Data API has no transcripts for anyone else's videos, no "most replayed"
heatmap, rounds nothing it does not have to, and runs on a 10,000-unit daily
budget in which a single search costs 100. yt-dlp reads the same public pages a
browser does, so this module covers:

- full video metadata: description, tags, chapters, the replay heatmap
- caption tracks — a creator's own subtitles first, then the auto-generated
  track in the language the video is spoken in
- a channel's videos / shorts / streams tab
- YouTube's own search results page (what a viewer actually sees)
- comments, when there is no API key to read them with

Every yt-dlp call here is synchronous; the tools run them through
``anyio.to_thread``. ``allowed_extractors`` pins yt-dlp to its YouTube
extractors, so an argument can never turn this into a fetch-any-URL primitive —
yt-dlp's generic extractor would otherwise follow whatever it was handed.

Nothing here invents a number. Where the public page rounds ("11K views"),
the value is the rounded one and the caller says so.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ID_IN_URL = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/|/v/)([A-Za-z0-9_-]{11})")
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._-]{3,100}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

TABS = ("videos", "shorts", "streams")


class ScrapeError(RuntimeError):
    """A readable reason the public page could not be read."""


def is_channel_id(ref: str) -> bool:
    return bool(_CHANNEL_ID_RE.fullmatch((ref or "").strip()))


# ── argument parsing ─────────────────────────────────────────────────────────

def _is_youtube_url(ref: str) -> bool:
    try:
        host = (urlparse(ref).hostname or "").lower()
    except ValueError:
        return False
    return host in _YT_HOSTS


def parse_video_id(ref: str) -> str:
    """An 11-character video id from an id, a watch/shorts/live/youtu.be URL."""
    ref = (ref or "").strip()
    if _ID_RE.fullmatch(ref):
        return ref
    if _is_youtube_url(ref):
        m = _ID_IN_URL.search(ref)
        if m:
            return m.group(1)
    raise ValueError(f"not a YouTube video id or URL: {ref!r}")


def channel_url(ref: str, tab: str = "") -> str:
    """The public channel URL for an @handle, a UC… id, or a channel URL."""
    ref = (ref or "").strip()
    if ref.startswith(("http://", "https://")):
        if not _is_youtube_url(ref):
            raise ValueError(f"not a YouTube channel URL: {ref!r}")
        base = ref.split("?")[0].split("#")[0].rstrip("/")
        base = re.sub(r"/(videos|shorts|streams|featured|about|playlists|community)$", "", base)
    elif _CHANNEL_ID_RE.fullmatch(ref):
        base = f"https://www.youtube.com/channel/{ref}"
    elif _HANDLE_RE.fullmatch(ref):
        base = f"https://www.youtube.com/@{ref.lstrip('@')}"
    else:
        raise ValueError(f"not a channel @handle, UC… id or channel URL: {ref!r}")
    return f"{base}/{tab}" if tab else base


# ── yt-dlp plumbing ──────────────────────────────────────────────────────────

class _Silent:
    """yt-dlp logs to stderr by default; errors still arrive as exceptions."""

    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _opts(**extra) -> dict:
    return {"quiet": True, "no_warnings": True, "skip_download": True,
            "noprogress": True, "socket_timeout": 20, "logger": _Silent(),
            "allowed_extractors": ["youtube.*"], **extra}


def _clean_error(e: Exception) -> str:
    msg = _ANSI.sub("", str(e)).replace("ERROR: ", "").strip()
    low = msg.lower()
    if "not a bot" in low or "sign in to confirm" in low:
        return ("YouTube is asking this server to prove it is not a bot — it has read "
                "too much, too fast. Wait a while and try again. (" + msg[:200] + ")")
    return msg[:400] or type(e).__name__


def _extract(url: str, **extra) -> dict:
    try:
        import yt_dlp
    except ImportError as e:              # pragma: no cover - dependency guard
        raise ScrapeError("yt-dlp is not installed — `pip install -r requirements.txt`") from e
    try:
        with yt_dlp.YoutubeDL(_opts(**extra)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise ScrapeError(_clean_error(e)) from e
    if not isinstance(info, dict):
        raise ScrapeError("YouTube returned nothing for that address")
    return info


def _date(yyyymmdd: str | None) -> str:
    s = str(yyyymmdd or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else ""


# ── videos ───────────────────────────────────────────────────────────────────

def video_info(video_id: str) -> dict:
    """Everything the public watch page exposes about one video, normalised."""
    info = _extract(f"https://www.youtube.com/watch?v={video_id}", noplaylist=True)
    return normalize_video(info)


def normalize_video(info: dict) -> dict:
    chapters = [{"start": float(c.get("start_time") or 0), "end": float(c.get("end_time") or 0),
                 "title": str(c.get("title") or "")} for c in (info.get("chapters") or [])]
    heat = [{"start": float(h.get("start_time") or 0), "end": float(h.get("end_time") or 0),
             "value": float(h.get("value") or 0)} for h in (info.get("heatmap") or [])]
    return {
        "id": info.get("id") or "",
        "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "channel_id": info.get("channel_id") or "",
        "handle": info.get("uploader_id") or "",
        "subscribers": info.get("channel_follower_count"),
        "views": info.get("view_count"),
        "likes": info.get("like_count"),
        "comments": info.get("comment_count"),
        "duration": info.get("duration"),
        "published": _date(info.get("upload_date")),
        "description": info.get("description") or "",
        "tags": list(info.get("tags") or []),
        "categories": list(info.get("categories") or []),
        "chapters": chapters,
        "heatmap": heat,
        "thumbnail": info.get("thumbnail") or "",
        "language": info.get("language") or "",
        "live_status": info.get("live_status") or "",
        "availability": info.get("availability") or "",
        "subtitles": info.get("subtitles") or {},
        "automatic_captions": info.get("automatic_captions") or {},
    }


# ── transcripts ──────────────────────────────────────────────────────────────

def _base(code: str) -> str:
    return (code or "").split("-")[0].lower()


def _json3(tracks: list) -> str:
    for t in tracks or []:
        if t.get("ext") == "json3" and t.get("url"):
            return t["url"]
    return ""


def pick_caption_track(subtitles: dict, automatic: dict, lang: str = "",
                       spoken: str = "") -> dict | None:
    """The caption track most worth reading, or None.

    Order: the creator's own subtitles in the wanted language; the auto track of
    the language actually spoken (``xx-orig`` — YouTube's speech recognition, not
    a translation); an auto track in the wanted language (machine-translated when
    that is not the spoken one); then any creator subtitles at all.
    """
    want = _base(lang) or _base(spoken)
    manual = {k: v for k, v in (subtitles or {}).items() if k != "live_chat" and _json3(v)}
    auto = {k: v for k, v in (automatic or {}).items() if _json3(v)}

    def first(pool: dict, pred) -> tuple[str, list] | None:
        for k, v in pool.items():
            if pred(k):
                return k, v
        return None

    candidates = []
    if want:
        candidates.append(("manual", first(manual, lambda k: _base(k) == want)))
    spoken_b = _base(spoken)
    if spoken_b and (not lang or _base(lang) == spoken_b):
        candidates.append(("auto", first(auto, lambda k: k.endswith("-orig") and _base(k) == spoken_b)))
    if want:
        candidates.append(("auto", first(auto, lambda k: _base(k) == want and not k.endswith("-orig"))))
    candidates.append(("manual", first(manual, lambda k: True)))
    candidates.append(("auto", first(auto, lambda k: k.endswith("-orig"))))
    for kind, hit in candidates:
        if hit:
            code, tracks = hit
            return {"lang": code.removesuffix("-orig"), "kind": kind, "url": _json3(tracks),
                    "translated": kind == "auto" and bool(spoken_b) and _base(code) != spoken_b}
    return None


def parse_json3(data: dict) -> list[tuple[float, str]]:
    """[(start_seconds, text)] from YouTube's json3 caption format."""
    out: list[tuple[float, str]] = []
    for ev in (data or {}).get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        text = " ".join("".join(s.get("utf8", "") for s in segs).split())
        if not text or (out and out[-1][1] == text):
            continue
        out.append((float(ev.get("tStartMs") or 0) / 1000.0, text))
    return out


def fetch_transcript(video: dict, lang: str = "") -> dict:
    """{"lang","kind","translated","segments"} for a normalised video, or raise."""
    track = pick_caption_track(video.get("subtitles") or {}, video.get("automatic_captions") or {},
                               lang, video.get("language") or "")
    if not track:
        raise ScrapeError("this video has no captions — neither the creator's nor YouTube's automatic ones")
    import httpx

    try:
        r = httpx.get(track["url"], timeout=20, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        segments = parse_json3(r.json())
    except Exception as e:
        raise ScrapeError(f"the caption track would not load: {_clean_error(e)}") from e
    if not segments:
        raise ScrapeError("the caption track is empty")
    return {**{k: track[k] for k in ("lang", "kind", "translated")}, "segments": segments}


def fmt_ts(seconds: float | int | None) -> str:
    s = int(seconds or 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def format_transcript(segments: list[tuple[float, str]], chapters: list[dict] | None = None,
                      *, timestamps: bool = True, every: float = 30.0) -> str:
    """Readable paragraphs: one timestamp per ~``every`` seconds, chapter headings inline.

    A timestamp on every caption line roughly doubles the token count for no gain;
    one per paragraph keeps the text citable ("at 4:12 he says…") and cheap.
    """
    chapters = sorted(chapters or [], key=lambda c: c["start"])
    ci = 0
    lines: list[str] = []
    para: list[str] = []
    para_start = None

    def flush():
        nonlocal para, para_start
        if para:
            body = " ".join(para)
            lines.append(f"[{fmt_ts(para_start)}] {body}" if timestamps else body)
        para, para_start = [], None

    for start, text in segments:
        while ci < len(chapters) and start >= chapters[ci]["start"]:
            flush()
            lines.append(f"\n### {chapters[ci]['title']} ({fmt_ts(chapters[ci]['start'])})")
            ci += 1
        if para_start is None:
            para_start = start
        para.append(text)
        if start - para_start >= every:
            flush()
    flush()
    return "\n".join(lines).strip()


def most_replayed(heatmap: list[dict], top: int = 5) -> list[dict]:
    """The highest non-adjacent peaks of the public replay heatmap.

    The opening bin is skipped: every view starts there, so it reads as a peak on
    nearly every video and says nothing about what people went back to.
    """
    if not heatmap:
        return []
    bins = heatmap[1:]
    ranked = sorted(range(len(bins)), key=lambda i: bins[i]["value"], reverse=True)
    picked: list[int] = []
    for i in ranked:
        if all(abs(i - j) > 2 for j in picked):
            picked.append(i)
        if len(picked) >= top:
            break
    return [bins[i] for i in sorted(picked)]


# ── channels & search ────────────────────────────────────────────────────────

def _entry(e: dict) -> dict:
    return {"id": e.get("id") or "", "title": e.get("title") or "",
            "views": e.get("view_count"), "duration": e.get("duration"),
            "channel": e.get("channel") or e.get("uploader") or "",
            "channel_id": e.get("channel_id") or "",
            "handle": e.get("uploader_id") or ""}


def channel_listing(ref: str, tab: str = "videos", limit: int = 30) -> dict:
    """A channel's tab, newest first. View counts are the page's (rounded) ones."""
    if tab not in TABS:
        raise ValueError(f"tab must be one of {TABS}")
    info = _extract(channel_url(ref, tab), extract_flat="in_playlist", playlistend=limit)
    entries = [_entry(e) for e in (info.get("entries") or [])[:limit] if e.get("id")]
    return {
        "channel": info.get("channel") or info.get("uploader") or "",
        "channel_id": info.get("channel_id") or "",
        "handle": info.get("uploader_id") or "",
        "subscribers": info.get("channel_follower_count"),
        "description": info.get("description") or "",
        "tags": list(info.get("tags") or []),
        "entries": entries,
    }


def search(query: str, limit: int = 10, order: str = "relevance") -> list[dict]:
    """YouTube's own results page for a query — the ranking a viewer sees."""
    prefix = "ytsearchdate" if order == "date" else "ytsearch"
    info = _extract(f"{prefix}{int(limit)}:{query}", extract_flat="in_playlist")
    return [_entry(e) for e in (info.get("entries") or []) if e.get("id")]


def comments(video_id: str, limit: int = 20, order: str = "top") -> list[dict]:
    """Top-level comments from the public page (slow — a few seconds a page)."""
    info = _extract(f"https://www.youtube.com/watch?v={video_id}", noplaylist=True,
                    getcomments=True,
                    extractor_args={"youtube": {"max_comments": [str(limit), str(limit), "0", "0"],
                                                "comment_sort": ["new" if order == "time" else "top"]}})
    out = []
    for c in info.get("comments") or []:
        if c.get("parent", "root") != "root":
            continue
        out.append({"author": c.get("author") or "", "likes": c.get("like_count") or 0,
                    "replies": None, "text": c.get("text") or "",
                    "published": c.get("timestamp"), "pinned": bool(c.get("is_pinned"))})
    return out[:limit]


# ── keyword suggestions (plain HTTP, no yt-dlp) ──────────────────────────────

# YouTube's own endpoint returns more suggestions than Google's ``ds=yt`` flavour
# of the same service (14 vs 10 for the same seed, measured), as JSONP.
YT_SUGGEST_URL = "https://suggestqueries-clients6.youtube.com/complete/search"
SUGGEST_URL = "https://suggestqueries.google.com/complete/search"


def parse_suggestions(body: Any) -> list[str]:
    """Suggestions from either shape the service answers in.

    ``client=firefox``: ``[query, ["s1", "s2", …], …]``.
    ``client=youtube``: ``window.google.ac.h([query, [["s1", 0, [..]], …], {…}])``.
    """
    if isinstance(body, str):
        start, end = body.find("("), body.rfind(")")
        if start < 0 or end <= start:
            return []
        try:
            import json
            body = json.loads(body[start + 1:end])
        except ValueError:
            return []
    if not (isinstance(body, list) and len(body) > 1 and isinstance(body[1], list)):
        return []
    out = []
    for s in body[1]:
        if isinstance(s, str):
            out.append(s)
        elif isinstance(s, list) and s and isinstance(s[0], str):
            out.append(s[0])
    return out
