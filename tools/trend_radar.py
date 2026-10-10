"""
tools/trend_radar.py: collectors, the Idea Studio's tools, title research, backfill
and the ComfyUI auto-benchmark for the Trend Radar.

Scoring, storage, backtest and idea merging live in ``core/trend_radar.py`` and are
network-free; everything that talks to the outside world is here. Each collector
fails on its own: Reddit rate-limiting this run must not cost the GitHub PRs.

What each source is for (lead time before weights, roughly):
  arXiv / HF papers          2-8 weeks    a method exists
  HF repo from a watched org 1-14 days    a release is being staged
  Diffusers / ComfyUI PR     1-10 days    the code is public, the model confirmed
  HF weights, then GGUF      0-2 days     runnable, then runnable on 4 GB
  Reddit / Hacker News       parallel     how excited people are
  Competitor YouTube         after        demand (views/day) and saturation
"""

import asyncio
import datetime as dt
import json
import re
import statistics
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from config import cfg
from core import trend_radar as tr

_ROOT = Path(__file__).resolve().parents[1]
NL = "\n"
UA = "PlutusMCP/1.0 (homelab MCP server; trend radar)"
TIMEOUT = httpx.Timeout(30.0)
HF = "https://huggingface.co/api"
GH = "https://api.github.com"
VISUAL_TASKS = {"text-to-video", "image-to-video", "video-to-video", "text-to-image",
                "image-to-image", "any-to-any", "image-text-to-video", "unconditional-image-generation"}
WEIGHT_EXT = (".safetensors", ".gguf", ".bin", ".pth", ".ckpt", ".pt")


# ── small helpers (module level so they can be unit-tested) ──────────────────

def iso_ts(value) -> int | None:
    """Epoch seconds from ISO-8601, epoch numbers, or None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def hf_signal(m: dict, *, source: str, quant: bool = False) -> dict | None:
    """One Hugging Face model listing row as a signal (None if not visual)."""
    rid = m.get("id") or m.get("modelId") or ""
    if not rid:
        return None
    tags = [str(t) for t in (m.get("tags") or [])]
    pipe = m.get("pipeline_tag") or ""
    name_low = rid.lower()
    visual = (pipe in VISUAL_TASKS or any(t in VISUAL_TASKS for t in tags)
              or any(w in name_low for w in ("video", "t2v", "i2v", "image", "diffusion", "flux", "wan", "ltx")))
    if not visual and not quant:
        return None
    # Quant uploaders also publish LLM GGUFs; those may refresh a known model but
    # must never invent a new candidate (see collect_hf).
    files = [s.get("rfilename", "") for s in (m.get("siblings") or [])]
    is_gguf = "gguf" in name_low or any(f.endswith(".gguf") for f in files) or "gguf" in tags
    has_weights = any(f.endswith(WEIGHT_EXT) for f in files)
    if quant:
        kind = "gguf" if (is_gguf or "fp8" in name_low or "nf4" in name_low or has_weights) else "repo"
    else:
        kind = "gguf" if is_gguf else ("weights" if has_weights else "repo")
    return {"source": source, "ext_id": rid, "kind": kind, "title": rid.replace("/", " / "),
            "match_text": " ".join(tags[:20]), "repo_id": rid,
            "url": f"https://huggingface.co/{rid}", "author": rid.split("/")[0],
            "ts": iso_ts(m.get("createdAt")) or int(time.time()),
            "metrics": {"likes": m.get("likes"), "downloads": m.get("downloads"),
                        "pipeline": pipe, "tags": tags[:20], "files": len(files)},
            "visual": bool(visual)}


def gh_pr_signal(pr: dict, repo: str) -> dict:
    return {"source": f"github:{repo}", "ext_id": str(pr.get("id") or pr.get("number")),
            "kind": "code", "title": pr.get("title") or "", "url": pr.get("html_url") or "",
            "author": ((pr.get("user") or {}).get("login") or ""),
            "match_text": (pr.get("body") or "")[:400],
            "ts": iso_ts(pr.get("created_at")) or int(time.time()),
            "metrics": {"merged": bool(pr.get("merged_at")), "state": pr.get("state"),
                        "comments": pr.get("comments")}}


def reddit_json_signals(body: dict, sub: str) -> list[dict]:
    out = []
    for ch in ((body or {}).get("data") or {}).get("children") or []:
        d = (ch or {}).get("data") or {}
        if not d.get("id"):
            continue
        out.append({"source": f"reddit:{sub}", "ext_id": d["id"], "kind": "buzz",
                    "title": d.get("title") or "", "match_text": (d.get("selftext") or "")[:600],
                    "url": "https://www.reddit.com" + (d.get("permalink") or ""),
                    "author": d.get("author") or "", "ts": int(d.get("created_utc") or time.time()),
                    "metrics": {"score": d.get("score"), "comments": d.get("num_comments"),
                                "text": (d.get("selftext") or "")[:300]}})
    return out


_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)


def _tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
    if not m:
        return ""
    txt = re.sub(r"<[^>]+>", " ", m.group(1))
    txt = txt.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\s+", " ", txt).strip()


def atom_signals(xml: str, *, source: str, kind: str) -> list[dict]:
    """Reddit search/listing feeds and the arXiv API are both Atom."""
    out = []
    for block in _ENTRY.findall(xml or ""):
        ident = _tag(block, "id")
        link = re.search(r'<link[^>]*href="([^"]+)"', block)
        when = _tag(block, "published") or _tag(block, "updated")
        if not ident:
            continue
        out.append({"source": source, "ext_id": ident.rsplit("/", 1)[-1] or ident, "kind": kind,
                    "title": _tag(block, "title"), "match_text": _tag(block, "summary")[:600],
                    "url": link.group(1) if link else ident, "author": _tag(block, "name"),
                    "ts": iso_ts(when) or int(time.time()), "metrics": {}})
    return out


def title_features(title: str) -> dict:
    t = title or ""
    words = t.split()
    return {
        "has_number": bool(re.search(r"\d", t)),
        "has_vram": bool(re.search(r"\b\d{1,2}\s?gb\b|vram|low.?end|laptop|potato", t, re.I)),
        "question": "?" in t,
        "caps_word": any(len(w) > 2 and w.isupper() for w in words),
        "brackets": bool(re.search(r"[\[\(]", t)),
        "free_local": bool(re.search(r"\bfree\b|\blocal\b|open.?source", t, re.I)),
        "short": len(t) <= 50,
    }


def pattern_report(rows: list[dict]) -> list[str]:
    """Which title features go with higher views/day in this set (median vs median).

    A tiny sample says little; the report prints the counts so nobody mistakes
    three videos for a law.
    """
    scored = [r for r in rows if r.get("vpd")]
    if len(scored) < 4:
        return ["(too few dated results to compare title patterns)"]
    lines = []
    for feat in title_features("").keys():
        yes = [r["vpd"] for r in scored if title_features(r["title"])[feat]]
        no = [r["vpd"] for r in scored if not title_features(r["title"])[feat]]
        if len(yes) >= 2 and len(no) >= 2:
            a, b = statistics.median(yes), statistics.median(no)
            ratio = a / b if b else float("inf")
            lines.append(f"- {feat}: median {a:,.0f}/day with vs {b:,.0f}/day without "
                         f"(x{ratio:.1f}, n={len(yes)}/{len(no)})")
    return lines or ["(no feature split had enough examples on both sides)"]


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _hf_headers() -> dict:
    h = {"User-Agent": UA}
    if getattr(cfg, "hf_token", ""):
        h["Authorization"] = f"Bearer {cfg.hf_token}"
    return h


def _gh_headers() -> dict:
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if getattr(cfg, "github_token", ""):
        h["Authorization"] = f"Bearer {cfg.github_token}"
    return h


async def _get(client: httpx.AsyncClient, url: str, **kw):
    r = await client.get(url, **kw)
    r.raise_for_status()
    return r


_reddit_last = [0.0]
_BACKGROUND: set = set()        # strong refs, or a fire-and-forget task can be GC'd mid-run


async def _reddit(client: httpx.AsyncClient, path: str, params: dict, *, sub: str) -> list[dict]:
    """Authenticated JSON when a Reddit login exists, else the Atom feed (no scores)."""
    from tools.social import reddit_token
    token = ""
    try:
        token = await reddit_token("")
    except Exception:
        token = ""
    if token:
        r = await _get(client, f"https://oauth.reddit.com{path}", params=params,
                       headers={"User-Agent": UA, "Authorization": f"bearer {token}"})
        return reddit_json_signals(r.json(), sub)
    wait = 2.0 - (time.monotonic() - _reddit_last[0])
    if wait > 0:
        await asyncio.sleep(wait)
    r = await client.get(f"https://www.reddit.com{path}.rss", params=params, headers={"User-Agent": UA})
    _reddit_last[0] = time.monotonic()
    r.raise_for_status()
    return atom_signals(r.text, source=f"reddit:{sub}", kind="buzz")


# ── collectors ───────────────────────────────────────────────────────────────

async def collect_hf(client, conf) -> list[tuple[dict, bool]]:
    out: list[tuple[dict, bool]] = []
    for org in conf["hf_orgs"]:
        r = await _get(client, f"{HF}/models", headers=_hf_headers(),
                       params={"author": org, "sort": "createdAt", "direction": -1,
                               "limit": 15, "full": "true"})
        for m in r.json():
            s = hf_signal(m, source=f"hf:{org}")
            if s:
                out.append((s, True))
    for author in conf["hf_quant_authors"]:
        r = await _get(client, f"{HF}/models", headers=_hf_headers(),
                       params={"author": author, "sort": "createdAt", "direction": -1,
                               "limit": 25, "full": "true"})
        for m in r.json():
            s = hf_signal(m, source=f"hfq:{author}", quant=True)
            if s:
                # A GGUF of a model nobody listed yet is often the first sign it
                # runs on small cards: learn its name, but only for visual models.
                out.append((s, s.get("visual", False)))
    return out


async def collect_hf_papers(client, conf) -> list[tuple[dict, bool]]:
    r = await _get(client, f"{HF}/daily_papers", params={"limit": 50}, headers=_hf_headers())
    out = []
    for p in r.json():
        paper = p.get("paper") or {}
        pid = paper.get("id") or ""
        if not pid:
            continue
        out.append(({"source": "hfpapers", "ext_id": pid, "kind": "paper",
                     "title": paper.get("title") or p.get("title") or "",
                     "match_text": (paper.get("summary") or "")[:600],
                     "url": f"https://huggingface.co/papers/{pid}",
                     "ts": iso_ts(paper.get("publishedAt") or p.get("publishedAt")) or int(time.time()),
                     "metrics": {"upvotes": paper.get("upvotes")}}, False))
    return out


async def collect_arxiv(client, conf) -> list[tuple[dict, bool]]:
    r = await _get(client, "https://export.arxiv.org/api/query", headers={"User-Agent": UA},
                   params={"search_query": conf["arxiv_query"], "sortBy": "submittedDate",
                           "sortOrder": "descending", "max_results": 40})
    return [(s, False) for s in atom_signals(r.text, source="arxiv", kind="paper")]


async def collect_github(client, conf) -> list[tuple[dict, bool]]:
    out = []
    for repo in conf["github_repos"]:
        r = await _get(client, f"{GH}/repos/{repo}/pulls", headers=_gh_headers(),
                       params={"state": "all", "sort": "created", "direction": "desc", "per_page": 40})
        out += [(gh_pr_signal(pr, repo), True) for pr in r.json()]
    for user in conf["github_users"]:
        r = await _get(client, f"{GH}/users/{user}/repos", headers=_gh_headers(),
                       params={"sort": "created", "direction": "desc", "per_page": 15})
        for repo in r.json():
            out.append(({"source": f"ghrepo:{user}", "ext_id": str(repo.get("id")), "kind": "repo",
                         "title": repo.get("name") or "", "match_text": repo.get("description") or "",
                         "url": repo.get("html_url") or "", "author": user,
                         "ts": iso_ts(repo.get("created_at")) or int(time.time()),
                         "metrics": {"stars": repo.get("stargazers_count")}}, False))
    return out


async def collect_reddit(client, conf) -> list[tuple[dict, bool]]:
    out = []
    for sub in conf["subreddits"]:
        for sort in ("new", "hot"):
            try:
                out += [(s, False) for s in await _reddit(client, f"/r/{sub}/{sort}",
                                                          {"limit": 50}, sub=sub)]
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 429:
                    raise
                break                       # rate-limited: keep what we have, move on
    return out


async def collect_hn(client, conf) -> list[tuple[dict, bool]]:
    out = []
    for q in ("video generation model", "open weights video", "text to video model"):
        r = await _get(client, "https://hn.algolia.com/api/v1/search_by_date",
                       params={"query": q, "tags": "story", "hitsPerPage": 30})
        for h in r.json().get("hits") or []:
            out.append(({"source": "hn", "ext_id": h.get("objectID"), "kind": "buzz",
                         "title": h.get("title") or "",
                         "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                         "author": h.get("author") or "", "ts": int(h.get("created_at_i") or time.time()),
                         "metrics": {"score": h.get("points"), "comments": h.get("num_comments")}}, False))
    return out


async def _channel_uploads(ref: str, kind_video: str) -> list[dict]:
    from tools.youtube import collect_channel
    ch = await collect_channel(ref, max_videos=15, max_shorts=10)
    out = []
    for u in ch.get("uploads") or []:
        kind = kind_video if u.get("kind") == "video" else ("own" if kind_video == "own" else "short")
        out.append({"source": f"youtube:{ch.get('handle') or ref}", "ext_id": u["id"], "kind": kind,
                    "title": u.get("title") or "", "url": f"https://www.youtube.com/watch?v={u['id']}",
                    "author": ch.get("title") or ref,
                    # Without an API key there is no publish date; first-seen time is
                    # stored once and never moved, which is accurate going forward.
                    "ts": iso_ts(u.get("published")) or int(time.time()),
                    "metrics": {"views": u.get("views"), "likes": u.get("likes"),
                                "comments": u.get("comments"), "subscribers": ch.get("subscribers"),
                                "thumb": f"https://i.ytimg.com/vi/{u['id']}/hqdefault.jpg"}})
    return out


async def collect_youtube(client, conf) -> list[tuple[dict, bool]]:
    out = []
    for ref in conf["youtube_channels"]:
        out += [(s, False) for s in await _channel_uploads(ref, "video")]
    if conf.get("my_channel"):
        out += [(s, False) for s in await _channel_uploads(conf["my_channel"], "own")]
    return out


COLLECTORS = {
    "huggingface": collect_hf,
    "hf_papers": collect_hf_papers,
    "arxiv": collect_arxiv,
    "github": collect_github,
    "reddit": collect_reddit,
    "hackernews": collect_hn,
    "youtube": collect_youtube,
}


def _enabled(name: str, conf: dict) -> bool:
    if name == "arxiv":
        return bool(conf.get("arxiv_enabled"))
    if name == "hf_papers":
        return bool(conf.get("hf_papers_enabled"))
    if name == "hackernews":
        return bool(conf.get("hn_enabled"))
    if name == "youtube":
        return bool(conf.get("youtube_channels") or conf.get("my_channel"))
    return True


async def run_collect(root: Path, *, only: list[str] | None = None, benchmark: bool = True) -> dict:
    """One full pass: every enabled collector, then rescore, alerts, auto-benchmark."""
    conf = tr.load_config(root)
    run_id = tr.start_run(root)
    detail: dict = {"sources": {}}
    if tr.matcher_outdated(root):
        detail["rematch"] = tr.rematch_all(root, conf)
    families = tr.known_families(root, conf)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        for name, fn in COLLECTORS.items():
            if (only and name not in only) or not _enabled(name, conf):
                continue
            t0 = time.monotonic()
            try:
                rows = await fn(client, conf)
                matched = 0
                for sig, allow_new in rows:
                    slug = tr.add_signal(root, sig, families=families, allow_new_family=allow_new)
                    if slug:
                        matched += 1
                        fam = tr.family_of(slug)
                        if fam not in families:
                            families.append(fam)
                detail["sources"][name] = {"ok": True, "seen": len(rows), "matched": matched,
                                           "secs": round(time.monotonic() - t0, 1)}
            except Exception as e:
                detail["sources"][name] = {"ok": False, "error": _short_error(e),
                                           "secs": round(time.monotonic() - t0, 1)}
    alerts = tr.rescore(root, conf)
    detail["alerts"] = [a["slug"] for a in alerts]
    if alerts and conf.get("alert_ntfy"):
        detail["ntfy"] = await _notify(alerts)
    if benchmark:
        detail["benchmark"] = await _auto_benchmark(root, conf)
    ok = any(v.get("ok") for v in detail["sources"].values()) if detail["sources"] else True
    tr.finish_run(root, run_id, ok, detail)
    detail["run_id"] = run_id
    return detail


def _short_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} from {e.request.url.host}"
    if isinstance(e, httpx.TimeoutException):
        return "timed out"
    return f"{type(e).__name__}: {str(e)[:160]}"


async def _notify(alerts: list[dict]) -> str:
    if not cfg.is_configured("ntfy_url"):
        return "ntfy not configured"
    lines = [f"{a['name']}: {a['hot']:.0f} hot, {a['stage']}, {a['action']}" for a in alerts[:5]]
    auth = (cfg.ntfy_username, cfg.ntfy_password) if cfg.ntfy_username and cfg.ntfy_password else None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{cfg.ntfy_url.rstrip('/')}/{cfg.ntfy_default_topic}",
                             content=NL.join(lines).encode(), auth=auth,
                             headers={"Title": "Trend Radar", "Tags": "satellite"})
            r.raise_for_status()
        return "sent"
    except Exception as e:
        return _short_error(e)


# ── ComfyUI auto-benchmark ───────────────────────────────────────────────────

def _fill(workflow: dict, seed: int) -> dict:
    text = json.dumps(workflow).replace('"{{seed}}"', str(seed)).replace("{{seed}}", str(seed))
    return json.loads(text)


def _vram(stats: dict) -> tuple[int, int]:
    dev = (stats.get("devices") or [{}])[0]
    return int(dev.get("vram_total") or 0), int(dev.get("vram_free") or 0)


def history_outcome(entry: dict) -> tuple[str, float | None, str]:
    """(status, seconds, detail) from one ComfyUI /history entry."""
    st = entry.get("status") or {}
    msgs = st.get("messages") or []
    t_start = t_end = None
    err = ""
    for m in msgs:
        if not isinstance(m, (list, tuple)) or len(m) < 2:
            continue
        name, data = m[0], m[1] or {}
        if name == "execution_start":
            t_start = data.get("timestamp")
        elif name in ("execution_success", "execution_error", "execution_interrupted"):
            t_end = data.get("timestamp")
            if name == "execution_error":
                err = f"{data.get('node_type', '')}: {data.get('exception_message', '')}".strip(": ")
    secs = round((t_end - t_start) / 1000, 1) if t_start and t_end else None
    if err:
        low = err.lower()
        status = "oom" if ("out of memory" in low or "outofmemory" in low or "allocation" in low) else "error"
        return status, secs, err[:600]
    if st.get("status_str") == "success" or st.get("completed"):
        return "ok", secs, ""
    return "error", secs, st.get("status_str") or "unknown"


async def run_benchmark(root: Path, slug: str, template: str, conf: dict) -> dict:
    """Queue the family's template on ComfyUI and time it. Real numbers only:
    if the run fails, that is the result (OOM is a result too on a 4 GB card)."""
    if not cfg.is_configured("comfyui_url"):
        return {"status": "skipped", "detail": "COMFYUI_URL not set"}
    tpl = (conf.get("benchmark") or {}).get("templates", {}).get(template)
    if not tpl:
        return {"status": "skipped", "detail": f"no template for {template}"}
    bid = tr.add_benchmark(root, slug, template)
    base = cfg.comfyui_url.rstrip("/")
    timeout_s = int((conf.get("benchmark") or {}).get("timeout_min", 120)) * 60
    seed = 42
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            total, min_free = _vram((await _get(c, f"{base}/system_stats")).json())
            r = await c.post(f"{base}/prompt", json={"prompt": _fill(tpl["workflow"], seed),
                                                     "client_id": uuid.uuid4().hex})
            if r.status_code == 400:
                body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                msg = json.dumps(body.get("node_errors") or body.get("error") or r.text)[:600]
                status = "missing_model" if "not in" in msg or "value_not_in_list" in msg else "invalid"
                tr.finish_benchmark(root, bid, status, detail=msg)
                return {"status": status, "detail": msg, "id": bid}
            r.raise_for_status()
            pid = r.json().get("prompt_id")
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout_s:
                await asyncio.sleep(5)
                try:
                    _, free = _vram((await _get(c, f"{base}/system_stats")).json())
                    min_free = min(min_free, free) if free else min_free
                except Exception:
                    pass
                h = (await _get(c, f"{base}/history/{pid}")).json()
                if pid in h:
                    status, secs, detail = history_outcome(h[pid])
                    secs = secs if secs is not None else round(time.monotonic() - t0, 1)
                    peak = total - min_free if total else None
                    tr.finish_benchmark(root, bid, status, seconds=secs, vram_peak=peak, detail=detail)
                    return {"status": status, "seconds": secs, "vram_peak": peak, "detail": detail, "id": bid}
            tr.finish_benchmark(root, bid, "timeout", detail=f"no result after {timeout_s // 60} min")
            return {"status": "timeout", "id": bid}
    except Exception as e:
        tr.finish_benchmark(root, bid, "error", detail=_short_error(e))
        return {"status": "error", "detail": _short_error(e), "id": bid}


async def _auto_benchmark(root: Path, conf: dict) -> str:
    b = conf.get("benchmark") or {}
    if not b.get("enabled") or not b.get("templates"):
        return "off"
    done = {x["slug"] for x in tr.benchmarks(root, limit=500)}
    for c in tr.candidates(root):
        if c["slug"] in done or c["stage"] not in ("weights", "gguf"):
            continue
        if c["hot"] < float(b.get("auto_min_hot", 75)):
            continue
        tpl = c["slug"] if c["slug"] in b["templates"] else c["family"]
        if tpl in b["templates"]:
            res = await run_benchmark(root, c["slug"], tpl, conf)
            return f"{c['slug']}: {res['status']}"       # one per pass; runs are long
    return "nothing eligible"


# ── title research ───────────────────────────────────────────────────────────

async def title_research(query: str, *, limit: int = 15, period: str = "month") -> dict:
    from core import youtube_scrape as ys
    from tools.youtube import _api, video_stats

    rows = await asyncio.to_thread(ys.search, query, limit, period)
    exact = False
    if cfg.youtube_api_key and rows:
        try:
            stats = await video_stats([r["id"] for r in rows])
            ch_ids = list({s["channel_id"] for s in stats.values() if s.get("channel_id")})
            subs: dict[str, int] = {}
            for i in range(0, len(ch_ids), 50):
                data = await _api("channels", {"part": "statistics", "id": ",".join(ch_ids[i:i + 50])})
                for it in data.get("items") or []:
                    n = (it.get("statistics") or {}).get("subscriberCount")
                    if n is not None:
                        subs[it["id"]] = int(n)
            now = time.time()
            for r in rows:
                s = stats.get(r["id"])
                if not s:
                    continue
                r["views"] = s["views"]
                r["published"] = s["published"]
                age = max((now - (iso_ts(s["published"] + "T00:00:00+00:00") or now)) / 86400, 0.5)
                r["vpd"] = (s["views"] or 0) / age
                r["subs"] = subs.get(s.get("channel_id", ""))
                if r.get("subs"):
                    r["views_per_sub"] = round((s["views"] or 0) / max(r["subs"], 1), 2)
            exact = True
        except Exception:
            exact = False
    for r in rows:
        r["thumb"] = f"https://i.ytimg.com/vi/{r['id']}/hqdefault.jpg"
        r["features"] = title_features(r["title"])
    return {"query": query, "period": period, "exact": exact, "rows": rows}


def render_research(res: dict) -> str:
    rows = sorted(res["rows"], key=lambda r: r.get("vpd") or 0, reverse=True)
    lines = [f"## Title research: {res['query']} ({res['period']})", ""]
    if not res["exact"]:
        lines += ["_No YouTube API key: views are the page's rounded numbers and there are no "
                  "publish dates, so views/day and the pattern split are unavailable._", ""]
    for r in rows:
        bits = [r.get("channel") or ""]
        if r.get("views") is not None:
            bits.append(f"{r['views']:,} views")
        if r.get("vpd"):
            bits.append(f"{r['vpd']:,.0f}/day")
        if r.get("views_per_sub") is not None:
            bits.append(f"{r['views_per_sub']}x subs (outperformance)")
        if r.get("published"):
            bits.append(r["published"])
        lines.append(f"- **{r['title']}**  ")
        lines.append(f"  {' · '.join(b for b in bits if b)}  ")
        lines.append(f"  thumb: {r['thumb']}  https://youtu.be/{r['id']}")
    lines += ["", "### Title patterns in this set"] + pattern_report(rows)
    return NL.join(lines)


# ── backfill (history import for the backtest) ───────────────────────────────

async def backfill(root: Path, name: str, *, include_youtube: bool = True) -> dict:
    """Pull dated history for one model from every source that keeps dates.

    Caveat that matters for honest backtests: Hugging Face ``createdAt`` is when a
    repo was *created*, which for private-then-public repos is before anyone could
    see it. Those rows are flagged (``hf_created_private_risk``) and the
    GitHub/Reddit dates are the safer anchors.
    """
    conf = tr.load_config(root)
    families = tr.known_families(root, conf)
    counts: dict[str, int | str] = {}
    q = name.strip()

    async def add(rows: list[dict], label: str, allow_new: bool = False):
        n = 0
        for s in rows:
            if tr.add_signal(root, s, families=families, allow_new_family=allow_new):
                n += 1
        counts[label] = n

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            r = await _get(client, f"{HF}/models", headers=_hf_headers(),
                           params={"search": q, "sort": "createdAt", "direction": 1, "limit": 60, "full": "true"})
            rows = []
            for m in r.json():
                author = (m.get("id") or "").split("/")[0]
                is_org = author in conf["hf_orgs"]
                s = hf_signal(m, source=f"hf:{author}" if is_org else f"hfq:{author}", quant=not is_org)
                if s and (is_org or s["kind"] == "gguf"):
                    s["metrics"]["hf_created_private_risk"] = is_org
                    rows.append(s)
            await add(rows, "huggingface", allow_new=True)
        except Exception as e:
            counts["huggingface"] = _short_error(e)
        try:
            rows = []
            for repo in conf["github_repos"]:
                r = await _get(client, f"{GH}/search/issues", headers=_gh_headers(),
                               params={"q": f'"{q}" repo:{repo} is:pr', "sort": "created",
                                       "order": "asc", "per_page": 30})
                rows += [gh_pr_signal(pr, repo) for pr in r.json().get("items") or []]
                await asyncio.sleep(2.5)            # search API: 10/min without a token
            await add(rows, "github")
        except Exception as e:
            counts["github"] = _short_error(e)
        try:
            rows = []
            for sub in conf["subreddits"]:
                rows += await _reddit(client, f"/r/{sub}/search",
                                      {"q": q, "restrict_sr": "1", "sort": "new", "t": "year", "limit": 100},
                                      sub=sub)
            await add(rows, "reddit")
        except Exception as e:
            counts["reddit"] = _short_error(e)
        try:
            r = await _get(client, "https://export.arxiv.org/api/query", headers={"User-Agent": UA},
                           params={"search_query": f'all:"{q}"', "sortBy": "submittedDate",
                                   "sortOrder": "ascending", "max_results": 20})
            await add(atom_signals(r.text, source="arxiv", kind="paper"), "arxiv")
        except Exception as e:
            counts["arxiv"] = _short_error(e)
    if include_youtube:
        try:
            res = await title_research(q, limit=25, period="year")
            mine = (conf.get("my_channel") or "").lstrip("@").lower()
            rows = []
            for v in res["rows"]:
                if not v.get("published"):
                    continue              # an undated video would land at "now" and poison the replay
                own = bool(mine) and mine in ((v.get("handle") or "") + (v.get("channel") or "")).lower().replace(" ", "")
                rows.append({"source": f"youtube:{v.get('handle') or v.get('channel')}", "ext_id": v["id"],
                             "kind": "own" if own else "video", "title": v["title"],
                             "url": f"https://www.youtube.com/watch?v={v['id']}",
                             "author": v.get("channel") or "",
                             "ts": iso_ts(v["published"] + "T12:00:00+00:00"),
                             "metrics": {"views": v.get("views"), "subscribers": v.get("subs"),
                                         "thumb": v["thumb"]}})
            await add(rows, "youtube")
            if not res["exact"]:
                counts["youtube_note"] = "no API key: videos skipped (no publish dates)"
        except Exception as e:
            counts["youtube"] = _short_error(e)
    tr.rescore(root, conf)
    return counts


# ── rendering ────────────────────────────────────────────────────────────────

def _ago(ts: int | None) -> str:
    if not ts:
        return "-"
    d = (time.time() - ts) / 86400
    if d < 0:
        return "future"
    if d < 1:
        return f"{d * 24:.0f}h ago"
    return f"{d:.0f}d ago"


def _date(ts: int | None) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "-"


def render_list(rows: list[dict]) -> str:
    if not rows:
        return "No candidates yet. Run trend_radar_collect first."
    lines = ["| # | model | hot | stage | action | signals | first seen |", "|---|---|---|---|---|---|---|"]
    for i, c in enumerate(rows, 1):
        lines.append(f"| {i} | {c['name']} (`{c['slug']}`) | {c['hot']:.0f} | {c['stage']} | {c['action']}"
                     f"{' · ' + c['status'] if c['status'] != 'watch' else ''} | {c['signal_count']} | "
                     f"{_ago(c['first_ts'])} |")
    return NL.join(lines)


def render_candidate(c: dict, sigs: list[dict], ideas: list[dict]) -> str:
    p = c.get("score") or {}
    lines = [f"## {c['name']} (`{c['slug']}`): {c['hot']:.0f} hot, {c['stage']}, {c['action']}", ""]
    if p:
        lines.append("Score parts: " + ", ".join(f"{k} {v}" for k, v in p.items() if v is not None))
        lines.append("")
    lines.append("### Timeline")
    for s in sigs[-60:]:
        mt = s.get("metrics") or {}
        extra = []
        for k in ("score", "comments", "views", "likes", "downloads"):
            if mt.get(k) is not None:
                extra.append(f"{k} {mt[k]:,}" if isinstance(mt[k], int) else f"{k} {mt[k]}")
        lines.append(f"- {_date(s['ts'])} · **{s['kind']}** · {s['source']} · {s['title'][:120]}"
                     f"{' · ' + ', '.join(extra) if extra else ''}  ")
        if s.get("url"):
            lines.append(f"  {s['url']}")
    if ideas:
        lines += ["", "### Ideas"] + [f"- #{i['id']} {i['title']} ({i['status']}, caught {i['catches']}x)"
                                      for i in ideas]
    return NL.join(lines)


def render_idea(i: dict) -> str:
    out = [f"## Idea #{i['id']}: {i['title']}",
           f"model `{i['candidate']}` · {i['status']} · hot {i['hot']:.0f} · caught {i['catches']}x"]
    if i.get("archive_reason"):
        out.append(f"archived: {i['archive_reason']}")
    for label, key in (("Alternative titles", "alt_titles"),):
        if i.get(key):
            out += ["", f"**{label}**"] + [f"- {t}" for t in i[key]]
    for label, key in (("Angle", "angle"), ("Hook", "hook"), ("Why now", "why_now"),
                       ("Thumbnail text", "thumb_text"), ("Thumbnail notes", "thumb_notes"),
                       ("Description", "description"), ("Script", "script"), ("Research", "research")):
        if i.get(key):
            out += ["", f"**{label}**", i[key]]
    if i.get("thumb_svg"):
        out += ["", f"_(thumbnail mock: {len(i['thumb_svg']):,} bytes of SVG, shown in the Trends tab)_"]
    if i.get("evidence"):
        out += ["", "**Evidence**"] + [f"- {e}" for e in i["evidence"]]
    return NL.join(out)


# ── MCP tools ────────────────────────────────────────────────────────────────

def register_trend_radar_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class CollectInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        sources: list[str] = Field(default_factory=list,
                                   description="Subset to run (huggingface, hf_papers, arxiv, github, "
                                               "reddit, hackernews, youtube). Empty = all enabled.")
        benchmark: bool = Field(default=True, description="Allow the auto-benchmark after scoring")
        wait: bool = Field(default=True, description="False: start in the background and return at once")

    @mcp.tool(name="trend_radar_collect",
              annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def trend_radar_collect(params: CollectInput) -> str:
        """Run the Trend Radar collectors now (HF orgs and GGUF quants, Diffusers/ComfyUI PRs,
        arXiv, HF papers, Reddit, Hacker News, competitor YouTube), then rescore every model.

        This is what the Trends schedule runs. Each source fails on its own.
        """
        if not params.wait:
            task = asyncio.get_running_loop().create_task(
                run_collect(_ROOT, only=params.sources or None, benchmark=params.benchmark))
            _BACKGROUND.add(task)
            task.add_done_callback(_BACKGROUND.discard)
            return "Trend Radar collect started in the background; see the Trends tab run log."
        d = await run_collect(_ROOT, only=params.sources or None, benchmark=params.benchmark)
        lines = ["## Trend Radar collect", ""]
        for k, v in d["sources"].items():
            lines.append(f"- {k}: " + (f"{v['seen']} seen, {v['matched']} matched ({v['secs']}s)"
                                       if v.get("ok") else f"FAILED: {v.get('error')}"))
        if d.get("alerts"):
            lines.append(f"- new alerts: {', '.join(d['alerts'])}")
        if d.get("benchmark"):
            lines.append(f"- benchmark: {d['benchmark']}")
        return NL.join(lines) + NL + NL + render_list(tr.candidates(_ROOT, limit=10))

    class ListInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        limit: int = Field(default=15, ge=1, le=100)
        actionable_only: bool = Field(default=False, description="Only PREP NOW / SHOOT ...")
        include_dismissed: bool = False

    @mcp.tool(name="trend_radar_list", annotations={"readOnlyHint": True})
    async def trend_radar_list(params: ListInput) -> str:
        """The radar's models ranked by hot score (0-100), with release stage
        (rumor > paper > repo > code > weights > gguf) and the suggested action."""
        rows = tr.candidates(_ROOT, include_dismissed=params.include_dismissed, limit=200)
        if params.actionable_only:
            rows = [r for r in rows if r["action"].startswith(("PREP", "SHOOT"))]
        return render_list(rows[:params.limit])

    class SlugInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        slug: str = Field(..., description="Candidate slug, e.g. wan-2.5", min_length=1, max_length=80)

    @mcp.tool(name="trend_radar_candidate", annotations={"readOnlyHint": True})
    async def trend_radar_candidate(params: SlugInput) -> str:
        """One model's evidence: score breakdown and every signal in time order with links."""
        c = tr.get_candidate(_ROOT, params.slug)
        if not c:
            return f"Error: no candidate {params.slug!r}. See trend_radar_list."
        return render_candidate(c, tr.signals_for(_ROOT, params.slug),
                                tr.ideas(_ROOT, candidate=params.slug, include_archived=True))

    class SignalsInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        limit: int = Field(default=40, ge=1, le=200)
        kind: str = Field(default="", description="buzz|paper|repo|code|weights|gguf|video|short|own")

    @mcp.tool(name="trend_radar_signals", annotations={"readOnlyHint": True})
    async def trend_radar_signals(params: SignalsInput) -> str:
        """The newest raw signals across all sources (matched or not)."""
        rows = tr.recent_signals(_ROOT, params.limit, params.kind)
        return NL.join(f"- {_date(s['ts'])} · {s['kind']} · {s['source']} · {s['title'][:110]}"
                       f" · → {s['candidate'] or '(unmatched)'}" for s in rows) or "No signals yet."

    class IdeasInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        status: str = Field(default="", description="new|shortlisted|producing|published|archived (empty = active)")
        candidate: str = Field(default="", max_length=80)
        limit: int = Field(default=30, ge=1, le=200)

    @mcp.tool(name="trend_radar_ideas", annotations={"readOnlyHint": True})
    async def trend_radar_ideas(params: IdeasInput) -> str:
        """Video ideas from the Idea Studio, best first (hot score plus how often
        the idea was independently caught). Archived ones only when asked for."""
        rows = tr.ideas(_ROOT, status=params.status, candidate=params.candidate, limit=params.limit)
        if not rows:
            return "No ideas yet."
        return NL.join(f"- #{i['id']} **{i['title']}** · `{i['candidate']}` · hot {i['hot']:.0f} · "
                       f"caught {i['catches']}x · {i['status']}"
                       f"{' (' + i['archive_reason'] + ')' if i.get('archive_reason') else ''}"
                       for i in rows)

    class IdeaInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        idea_id: int = Field(..., ge=1)

    @mcp.tool(name="trend_radar_idea", annotations={"readOnlyHint": True})
    async def trend_radar_idea(params: IdeaInput) -> str:
        """One idea in full: titles, hook, description, script, thumbnail notes, research."""
        i = tr.get_idea(_ROOT, params.idea_id)
        return render_idea(i) if i else f"Error: no idea #{params.idea_id}."

    class SaveIdeaInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        candidate: str = Field(..., description="Candidate slug from trend_radar_list", max_length=80)
        title: str = Field(..., min_length=3, max_length=200)
        alt_titles: list[str] = Field(default_factory=list, max_length=6)
        angle: str = Field(default="", max_length=400, description="The video's angle in one line")
        hook: str = Field(default="", max_length=1000, description="First 15 seconds")
        why_now: str = Field(default="", max_length=2000)
        description: str = Field(default="", max_length=8000, description="Draft YouTube description")
        script: str = Field(default="", max_length=40000, description="Script outline or draft")
        thumb_text: str = Field(default="", max_length=120)
        thumb_notes: str = Field(default="", max_length=2000)
        thumb_svg: str = Field(default="", max_length=200000,
                               description="A single <svg viewBox='0 0 1280 720'> thumbnail mock, no scripts")
        evidence: list[str] = Field(default_factory=list, max_length=30, description="Source URLs")
        research: str = Field(default="", max_length=12000,
                              description="What worked for other creators' titles/thumbnails")

    @mcp.tool(name="trend_radar_save_idea",
              annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
    async def trend_radar_save_idea(params: SaveIdeaInput) -> str:
        """Save a video idea for a radar model. If it duplicates an active idea on the
        same model it is merged in (titles become alternatives, catches +1, which
        raises the model's hot score) instead of creating a second card."""
        try:
            i = tr.save_idea(_ROOT, params.model_dump())
        except (ValueError, KeyError) as e:
            return f"Error: {e}"
        tr.rescore(_ROOT, tr.load_config(_ROOT))
        return (f"{'Merged into' if i['merged'] else 'Saved as'} idea #{i['id']}: {i['title']} "
                f"(caught {i['catches']}x)")

    class StatusInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        idea_id: int = Field(default=0, ge=0, description="Set an idea's status (or 0)")
        slug: str = Field(default="", max_length=80, description="…or a model's status")
        status: str = Field(..., description="ideas: new|shortlisted|producing|published|archived; "
                                             "models: watch|pinned|dismissed|covered")
        reason: str = Field(default="", max_length=300, description="Why (required when archiving)")
        merge_into: str = Field(default="", max_length=80,
                                description="Models only: fold this slug into another (duplicate names)")

    @mcp.tool(name="trend_radar_set_status",
              annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
    async def trend_radar_set_status(params: StatusInput) -> str:
        """Shortlist / archive an idea, or pin / dismiss / merge a model. Nothing is deleted."""
        try:
            if params.idea_id:
                upd = {"status": params.status}
                if params.status == "archived":
                    upd["archive_reason"] = params.reason or "archived over MCP"
                i = tr.set_idea(_ROOT, params.idea_id, upd)
                return f"Idea #{i['id']} is now {i['status']}."
            if params.slug and params.merge_into:
                n = tr.merge(_ROOT, params.slug, params.merge_into)
                tr.rescore(_ROOT, tr.load_config(_ROOT))
                return f"Merged {params.slug} into {params.merge_into} ({n} signals moved)."
            if params.slug:
                c = tr.set_status(_ROOT, params.slug, params.status, params.reason or None)
                tr.rescore(_ROOT, tr.load_config(_ROOT))
                return f"{c['name']} is now {c['status']}."
        except (ValueError, KeyError) as e:
            return f"Error: {e}"
        return "Error: give idea_id or slug."

    class StudioFileInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., min_length=1, max_length=300,
                          description="Relative to the studio folder, e.g. .claude/skills/thumbnail/references/style.md "
                                      "or knowledge/analytics.csv")

    @mcp.tool(name="trend_radar_studio_file", annotations={"readOnlyHint": True})
    async def trend_radar_studio_file(params: StudioFileInput) -> str:
        """Read one file from the Idea Studio folder: skill references and knowledge files."""
        try:
            return tr.read_studio_file(_ROOT, params.path)
        except FileNotFoundError:
            files = [f"knowledge/{k['name']}" for k in tr.list_knowledge(_ROOT)]
            return f"Error: no such file. Knowledge files: {', '.join(files) or 'none'}"

    @mcp.tool(name="trend_radar_studio_run",
              annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def trend_radar_studio_run() -> str:
        """Start an Idea Studio run now (what the daily schedule calls). The prompt is
        built at this moment, so skills and knowledge uploaded since are included."""
        from tools import production as tp
        fn = tp.EXPLAINER_LAUNCHER["fn"]
        if not fn:
            return "Error: studio runs start from the Plutus dashboard or its scheduler (Trends > Studio > Run now)."
        fn(tr.studio_prompt(tr.load_config(_ROOT), _ROOT), "sched:Idea Studio")
        return "Idea Studio run queued."

    class DoneInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        apply: bool = Field(default=False, description="True: mark every suggestion done")

    @mcp.tool(name="trend_radar_done",
              annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
    async def trend_radar_done(params: DoneInput) -> str:
        """Ideas and models your own channel already covered (cross-referenced with
        your uploads). With apply=true, marks them all published / covered."""
        if params.apply:
            r = tr.apply_done(_ROOT)
            tr.rescore(_ROOT, tr.load_config(_ROOT))
            return f"Marked {r['ideas']} ideas published and {r['candidates']} models covered."
        sug = tr.suggest_done(_ROOT)
        if not sug["ideas"] and not sug["candidates"]:
            return (f"Nothing looks finished ({sug['own_videos_seen']} of your uploads seen; "
                    "set my_channel in the Trends settings if that is 0).")
        lines = ["## Looks finished", ""]
        lines += [f"- idea #{s['idea_id']} {s['idea_title']} ← {s['video_title']} ({s['reason']})"
                  for s in sug["ideas"]]
        lines += [f"- model {c['name']} ← {c['video']}" for c in sug["candidates"]]
        return NL.join(lines + ["", "Call again with apply=true to mark them all."])

    class ResearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., min_length=2, max_length=120, description="e.g. 'Wan 2.5 ComfyUI'")
        period: str = Field(default="month", description="today|week|month|year|relevance")
        limit: int = Field(default=15, ge=3, le=40)

    @mcp.tool(name="trend_radar_title_research", annotations={"readOnlyHint": True, "openWorldHint": True})
    async def trend_radar_title_research(params: ResearchInput) -> str:
        """What other creators titled their videos on a topic, with thumbnail links,
        views/day and views-per-subscriber (who over-performed), plus which title
        patterns went with more views in this set. Exact numbers need YOUTUBE_API_KEY."""
        period = params.period if params.period in ("today", "week", "month", "year", "relevance") else "month"
        try:
            res = await title_research(params.query, limit=params.limit, period=period)
        except Exception as e:
            return f"Error: YouTube search failed ({_short_error(e)})"
        return render_research(res)

    class BackfillInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        name: str = Field(..., min_length=2, max_length=80, description="Model name as people write it, e.g. 'Wan 2.2'")
        include_youtube: bool = True

    @mcp.tool(name="trend_radar_backfill",
              annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def trend_radar_backfill(params: BackfillInput) -> str:
        """Import a past model's dated history (HF, GitHub PRs, Reddit, arXiv, YouTube)
        so trend_radar_backtest can replay it. Slow: GitHub search is rate-limited."""
        counts = await backfill(_ROOT, params.name, include_youtube=params.include_youtube)
        return f"Backfilled '{params.name}': " + ", ".join(f"{k} {v}" for k, v in counts.items())

    class BacktestInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        slug: str = Field(default="", max_length=80, description="One model, or empty for all with a release")

    @mcp.tool(name="trend_radar_backtest", annotations={"readOnlyHint": True})
    async def trend_radar_backtest(params: BacktestInput) -> str:
        """Replay history with today's scoring rules: when would the radar have alerted,
        how long before the weights and before the first competitor video, and did it
        catch the models you actually covered (recall) without crying wolf (precision)."""
        conf = tr.load_config(_ROOT)
        if params.slug:
            c = tr.get_candidate(_ROOT, params.slug)
            if not c:
                return f"Error: no candidate {params.slug!r}."
            r = tr.backtest(tr.signals_for(_ROOT, params.slug), conf, family=c["family"])
            if not r.get("ok"):
                return f"Error: {r.get('error')}"
            return NL.join([f"## Backtest {c['name']}", "",
                            f"- first signal: {_date(r['first_signal'])}",
                            f"- alert (≥{r['threshold']:.0f}): {_date(r['alert'])}",
                            f"- actionable (PREP/SHOOT): {_date(r['actionable'])}",
                            f"- weights out: {_date(r['release'])}",
                            f"- first competitor video: {_date(r['first_competitor_video'])}",
                            f"- your upload: {_date(r['own_upload'])} (rank {r['own_rank'] or '-'})",
                            f"- lead alert→weights: {r['lead_alert_to_release_h']} h",
                            f"- lead alert→first competitor: {r['lead_alert_to_first_video_h']} h"])
        res = tr.backtest_all(_ROOT, conf)
        s = res["summary"]
        lines = ["## Backtest, all released models", "",
                 f"- models with a release: {s['candidates_with_release']}",
                 f"- you covered: {s['covered_by_you']}, recall {s['recall_on_your_videos']}",
                 f"- precision (alert → real release): {s['precision_alert_became_release']}",
                 f"- median lead alert→weights: {s['median_lead_alert_to_release_h']} h", ""]
        for r in res["rows"]:
            lines.append(f"- {r['name']}: alert {_date(r['alert'])}, weights {_date(r['release'])}, "
                         f"first competitor {_date(r['first_competitor_video'])}, you {_date(r['own_upload'])}")
        return NL.join(lines)

    class BenchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        slug: str = Field(..., max_length=80)
        template: str = Field(default="", max_length=80, description="Template name (default: the model's family)")

    @mcp.tool(name="trend_radar_benchmark",
              annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def trend_radar_benchmark(params: BenchInput) -> str:
        """Queue the model's benchmark workflow on ComfyUI (COMFYUI_URL) and record real
        wall time, peak VRAM and the outcome (ok / oom / missing_model / error)."""
        conf = tr.load_config(_ROOT)
        c = tr.get_candidate(_ROOT, params.slug)
        if not c:
            return f"Error: no candidate {params.slug!r}."
        res = await run_benchmark(_ROOT, params.slug, params.template or c["family"], conf)
        return "Benchmark: " + json.dumps(res)
