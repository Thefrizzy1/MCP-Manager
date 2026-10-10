"""Trend Radar: catch a new model before the crowd does, and prove it would have.

The radar is three pieces, and only the collectors touch the network:

- **Signals.** Every observation is one row: a pull request titled "Add Wan 2.5
  pipeline", a new Hugging Face repo from a watched org, a GGUF upload, a Reddit
  thread, a competitor's video. Each carries the time it *happened* (``ts``), not
  the time we saw it, because the backtest replays history by that clock.
- **Candidates.** Signals that name the same model are grouped under one slug
  (``wan-2.5``). Matching is a deterministic regex over a family list, never an
  LLM: a model that already knows how past launches went would quietly leak the
  answer into any backtest it scored.
- **Score.** A 0-100 "hot" number from components you can see and re-weight:
  release stage, momentum, community excitement, viewer demand, niche fit, and a
  saturation penalty for how many creators already covered it. Low-VRAM coverage
  is counted separately, because for this channel a model everyone covered on a
  4090 is still an open story on 4 GB.

Everything here is pure (SQLite + arithmetic) so the scorer and the replay can be
tested offline. ``tools/trend_radar.py`` owns the collectors and the MCP tools.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path

from core.atomic_json import read_json, write_json

DB_FILE = "trend_radar.sqlite3"
CONFIG_FILE = "trend_radar.json"
_LOCK = threading.Lock()

# ── stages ───────────────────────────────────────────────────────────────────
# Ordered by how close a model is to being runnable. ``video``/``short``/``own``
# are coverage, not release progress, so they never move the stage.
STAGES = ("rumor", "paper", "repo", "code", "weights", "gguf")
STAGE_OF_KIND = {"buzz": "rumor", "paper": "paper", "repo": "repo", "code": "code",
                 "weights": "weights", "gguf": "gguf"}
COVERAGE_KINDS = ("video", "short", "own")
# How valuable each stage is *to be early on*. Code is the sweet spot: the model
# is confirmed and public, the weights are days away, nobody has a video yet.
STAGE_VALUE = {"rumor": 0.2, "paper": 0.35, "repo": 0.6, "code": 0.85,
               "weights": 1.0, "gguf": 0.95}
# Momentum weight per signal kind: a merged pipeline PR says more than a meme.
KIND_WEIGHT = {"buzz": 1.0, "paper": 2.0, "repo": 2.5, "code": 3.0, "weights": 4.0,
               "gguf": 3.0, "video": 1.0, "short": 0.5, "own": 0.0}

DEFAULT_CONFIG: dict = {
    # Model families the matcher recognises. Longer names win ("hunyuanvideo"
    # before "hunyuan"). Families found on watched orgs are added automatically.
    "families": [
        "wan", "ltx", "ltxv", "hunyuanvideo", "hunyuan", "flux", "qwen-image",
        "z-image", "cogvideox", "mochi", "skyreels", "framepack", "vace", "magi",
        "kandinsky", "step-video", "seedvr", "chroma", "hidream", "omnigen",
        "longcat", "lumina", "sana", "cosmos", "ovi", "infinitetalk", "multitalk",
        "fantasytalking", "uni3c", "phantom", "minimax", "kling", "veo", "sora",
        "seedance", "hailuo", "pixverse", "ideogram",
    ],
    # Closed/API-only families: still worth watching, but a local-first channel
    # cannot benchmark them, so niche fit is lowered.
    "closed_families": ["kling", "veo", "sora", "seedance", "hailuo", "pixverse",
                        "ideogram", "minimax"],
    "hf_orgs": ["Wan-AI", "Lightricks", "tencent", "Qwen", "black-forest-labs",
                "alibaba-pai", "Tongyi-MAI", "stepfun-ai", "zai-org", "genmo",
                "Skywork", "lllyasviel", "ByteDance", "ByteDance-Seed",
                "meituan-longcat", "nvidia", "stabilityai", "character-ai"],
    "hf_quant_authors": ["city96", "QuantStack", "unsloth", "Kijai", "calcuis",
                         "gguf-org", "bullerwins"],
    # Repos whose pull requests announce a model before its weights land.
    "github_repos": ["huggingface/diffusers", "comfyanonymous/ComfyUI",
                     "kijai/ComfyUI-WanVideoWrapper", "city96/ComfyUI-GGUF",
                     "Lightricks/ComfyUI-LTXVideo", "kijai/ComfyUI-KJNodes"],
    # Accounts whose *new repos* are a signal (a wrapper appears on release day).
    "github_users": ["kijai"],
    "subreddits": ["StableDiffusion", "comfyui", "LocalLLaMA", "aivideo"],
    # Competitor channels: @handle, channel URL or UC… id.
    "youtube_channels": [],
    "my_channel": "@the_frizzy1",
    "arxiv_enabled": True,
    "arxiv_query": 'cat:cs.CV AND (abs:"video generation" OR abs:"video diffusion" '
                   'OR abs:"image-to-video" OR abs:"text-to-video")',
    "hf_papers_enabled": True,
    "hn_enabled": True,
    # Words that mark a thread as excited rather than just informative.
    "hype_words": ["insane", "incredible", "game changer", "game-changer", "wow",
                   "finally", "huge", "sota", "state of the art", "mind blowing",
                   "mindblowing", "crazy", "beats", "better than", "open source",
                   "open-source", "weights released", "just dropped", "released",
                   "coming soon", "leak", "teaser"],
    # Terms that mark a creator video as a low-VRAM take on the model.
    "lowvram_words": ["4gb", "6gb", "8gb", "low vram", "lowvram", "gguf", "potato",
                      "laptop", "rtx 3050", "rtx 3060", "weak gpu", "cheap gpu"],
    "weights": {"stage": 30, "momentum": 15, "buzz": 20, "demand": 15, "fit": 20,
                "saturation": 20},
    "alert_threshold": 70,
    "alert_ntfy": False,
    "schedule_cron": "0 */3 * * *",
    "schedule_enabled": True,
    # The daily agent run that turns hot candidates into video ideas.
    "studio_enabled": False,
    "studio_cron": "30 6 * * *",
    "studio_max_candidates": 4,
    "studio_model": "",
    "studio_prompt": "",
    # VibeVoice through ComfyUI: an API-format workflow with {{text}} (plain
    # narration), {{line}} ("[N]: narration") and {{seed}} placeholders.
    "voice": {"template": {}, "timeout_min": 20},
    # Where finished packages are copied (a NAS share the Windows PC can see).
    "export_dir": "",
    # Wake the ComfyUI machine before a night run (Wake-on-LAN).
    "wake": {"mac": "", "broadcast": "255.255.255.255", "wait_min": 4},
    # Off by default: every step is a button. On, the night run voices, renders
    # and packages productions you approved for auto, and drafts explainers for
    # shortlisted ideas if that switch is on too.
    "auto": {"enabled": False, "cron": "0 2 * * *", "explainers": False, "voice": True,
             "render": True, "package": True, "max_items": 2},
    "benchmark": {
        "enabled": False,
        "auto_min_hot": 75,
        "timeout_min": 120,
        # family -> {"workflow": <ComfyUI API-format JSON>, "note": str}
        "templates": {},
    },
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL,
    ext_id    TEXT NOT NULL,
    kind      TEXT NOT NULL,
    title     TEXT NOT NULL DEFAULT '',
    url       TEXT NOT NULL DEFAULT '',
    author    TEXT NOT NULL DEFAULT '',
    ts        INTEGER NOT NULL,
    seen      INTEGER NOT NULL,
    metrics   TEXT NOT NULL DEFAULT '{}',
    candidate TEXT NOT NULL DEFAULT '',
    UNIQUE (source, ext_id)
);
CREATE INDEX IF NOT EXISTS signals_cand ON signals (candidate, ts);
CREATE INDEX IF NOT EXISTS signals_ts ON signals (ts);
CREATE TABLE IF NOT EXISTS candidates (
    slug      TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    family    TEXT NOT NULL DEFAULT '',
    first_ts  INTEGER NOT NULL,
    status    TEXT NOT NULL DEFAULT 'watch',
    notes     TEXT NOT NULL DEFAULT '',
    hot       REAL NOT NULL DEFAULT 0,
    stage     TEXT NOT NULL DEFAULT 'rumor',
    action    TEXT NOT NULL DEFAULT 'WATCH',
    score     TEXT NOT NULL DEFAULT '{}',
    alerted   INTEGER NOT NULL DEFAULT 0,
    updated   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started   INTEGER NOT NULL,
    finished  INTEGER NOT NULL DEFAULT 0,
    ok        INTEGER NOT NULL DEFAULT 0,
    detail    TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ideas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate   TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    alt_titles  TEXT NOT NULL DEFAULT '[]',
    angle       TEXT NOT NULL DEFAULT '',
    hook        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    script      TEXT NOT NULL DEFAULT '',
    thumb_svg   TEXT NOT NULL DEFAULT '',
    thumb_text  TEXT NOT NULL DEFAULT '',
    thumb_notes TEXT NOT NULL DEFAULT '',
    why_now     TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '[]',
    research    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'new',
    archive_reason TEXT NOT NULL DEFAULT '',
    catches     INTEGER NOT NULL DEFAULT 1,
    hot         REAL NOT NULL DEFAULT 0,
    created     INTEGER NOT NULL,
    updated     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ideas_cand ON ideas (candidate);
CREATE TABLE IF NOT EXISTS benchmarks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    slug      TEXT NOT NULL,
    template  TEXT NOT NULL DEFAULT '',
    started   INTEGER NOT NULL,
    finished  INTEGER NOT NULL DEFAULT 0,
    status    TEXT NOT NULL DEFAULT 'queued',
    seconds   REAL,
    vram_peak INTEGER,
    detail    TEXT NOT NULL DEFAULT ''
);
"""

STATUSES = ("watch", "pinned", "dismissed", "covered")


# ── config ───────────────────────────────────────────────────────────────────

def config_path(root: Path) -> Path:
    p = Path(root) / "data" / CONFIG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_config(root: Path) -> dict:
    """Stored config laid over the defaults, one level deep for the dict keys.

    Merging onto the *defaults* (not replacing them) means a new default key
    shows up after an upgrade without the user re-saving anything.
    """
    stored = read_json(config_path(root), {}) or {}
    out = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in stored.items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def save_config(root: Path, updates: dict) -> dict:
    """Merge a *partial* update onto the stored config, then validate.

    Partial saves merge onto what is on disk, not onto the defaults; the other
    way round, saving one field silently reset every other field the user had
    changed (a bug the Model Radar app shipped with once).
    """
    current = load_config(root)
    for k, v in (updates or {}).items():
        if k not in DEFAULT_CONFIG:
            raise ValueError(f"unknown setting: {k}")
        if isinstance(current.get(k), dict) and isinstance(v, dict):
            current[k] = {**current[k], **v}
        else:
            current[k] = v
    _validate(current)
    write_json(config_path(root), current)
    return current


def _validate(c: dict) -> None:
    list_keys = ("families", "closed_families", "hf_orgs", "hf_quant_authors",
                 "github_repos", "github_users", "subreddits", "youtube_channels",
                 "hype_words", "lowvram_words")
    for k in list_keys:
        if not isinstance(c.get(k), list) or not all(isinstance(x, str) for x in c[k]):
            raise ValueError(f"{k} must be a list of strings")
        c[k] = [x.strip() for x in c[k] if x.strip()][:200]
    for r in c["github_repos"]:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", r):
            raise ValueError(f"github repo must look like owner/name: {r}")
    w = c.get("weights")
    if not isinstance(w, dict) or any(not isinstance(v, (int, float)) or v < 0 for v in w.values()):
        raise ValueError("weights must be non-negative numbers")
    t = c.get("alert_threshold")
    if not isinstance(t, (int, float)) or not 0 <= t <= 100:
        raise ValueError("alert_threshold must be 0-100")
    if not isinstance(c.get("studio_max_candidates"), int) or not 1 <= c["studio_max_candidates"] <= 15:
        raise ValueError("studio_max_candidates must be 1-15")
    for k in ("studio_prompt", "studio_model", "studio_cron", "schedule_cron", "my_channel", "arxiv_query"):
        if not isinstance(c.get(k), str):
            raise ValueError(f"{k} must be text")
    from core.schedule_store import validate_cron
    for k in ("schedule_cron", "studio_cron"):
        ok, err = validate_cron(c[k])
        if not ok:
            raise ValueError(f"{k}: {err}")
    a = c.get("auto")
    if not isinstance(a, dict):
        raise ValueError("auto must be an object")
    ok, err = validate_cron(str(a.get("cron", "")))
    if not ok:
        raise ValueError(f"auto.cron: {err}")
    v = c.get("voice")
    if not isinstance(v, dict) or not isinstance(v.get("template", {}), dict):
        raise ValueError("voice.template must be ComfyUI API-format JSON")
    if v.get("template") and "{{text}}" not in json.dumps(v["template"]) and "{{line}}" not in json.dumps(v["template"]):
        raise ValueError("voice.template needs a {{text}} or {{line}} placeholder where the narration goes")
    w = c.get("wake") or {}
    if w.get("mac") and not re.fullmatch(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", w["mac"]):
        raise ValueError("wake.mac must look like AA:BB:CC:DD:EE:FF")
    if not isinstance(c.get("export_dir"), str):
        raise ValueError("export_dir must be text")
    b = c.get("benchmark")
    if not isinstance(b, dict) or not isinstance(b.get("templates", {}), dict):
        raise ValueError("benchmark.templates must be an object")
    for fam, tpl in b.get("templates", {}).items():
        if not isinstance(tpl, dict) or not isinstance(tpl.get("workflow"), dict):
            raise ValueError(f"benchmark template {fam}: workflow must be ComfyUI API-format JSON")


# ── matching ─────────────────────────────────────────────────────────────────

def _family_pattern(family: str) -> str:
    # "qwen-image" matches "Qwen Image", "Qwen-Image" and "QwenImage".
    parts = [re.escape(p) for p in re.split(r"[-_ ]", family.lower()) if p]
    return r"[-_ ]?".join(parts)


_VERSION = r"[\s\-_.]*v?(\d{1,2}(?:\.\d{1,2}){0,2})(?![\d])"
_NOT_A_VERSION = re.compile(r"^\s*(?:b\b|m\b|k\b|gb|fps|p\b|x\d|s\b|bit|steps?)", re.I)


def match(text: str, families: list[str]) -> tuple[str, str, str] | None:
    """(slug, display name, family) for the model named in ``text``.

    A versioned mention beats a bare one anywhere in the text ("Wan-AI /
    Wan2.2-T2V" is Wan 2.2, not the org name). The version is optional;
    ``14B``/``720p``/``8GB`` after a family are size or resolution and are
    rejected, so "Wan 14B" stays ``wan``.
    """
    if not text:
        return None
    low = text.lower()
    found: list[tuple[int, int, int, str, str | None, str]] = []
    for fam in sorted({f.lower() for f in families if f}, key=len, reverse=True):
        pat = re.compile(r"(?<![a-z0-9])(" + _family_pattern(fam) + r")(?![a-z])(?:" + _VERSION + r")?")
        for m in pat.finditer(low):
            ver = m.group(2)
            if ver and _NOT_A_VERSION.match(low[m.end(2):]):
                ver = None
            # versioned first, then earliest, then the longer family name
            found.append((0 if ver else 1, m.start(1), -len(fam), fam, ver, text[m.start(1):m.end(1)]))
    if not found:
        return None
    _, _, _, fam, ver, raw = min(found)
    ver = ver.rstrip(".") if ver else None
    slug = f"{fam}-{ver}" if ver else fam
    name = (raw if raw[:1].isupper() else fam.title()) + (f" {ver}" if ver else "")
    return slug, name, fam


_REPO_NOISE = re.compile(
    r"[-_](?:t2v|i2v|ti2v|s2v|v2v|flf2v|fun|a?\d+(?:\.\d+)?b|diffusers|gguf|fp8|fp16|bf16|"
    r"int4|int8|nf4|q\d\w*|dev|schnell|distill(?:ed)?|preview|instruct|base|hf|"
    r"\d{3,4}p|turbo|lite|full|comfy(?:ui)?|lora|controlnet|vae|merged?)\b",
    re.I)


def name_from_repo(repo_id: str) -> tuple[str, str] | None:
    """A new family's (slug, name) from a repo id when no known family matched.

    ``meituan-longcat/LongCat-Video-Base`` -> (``longcat-video``, ``LongCat-Video``).
    Only used for watched orgs, so a random repo cannot invent a candidate.
    """
    base = repo_id.split("/")[-1]
    prev = None
    while prev != base:
        prev = base
        base = _REPO_NOISE.sub("", base)
    base = base.strip("-_. ")
    if len(base) < 3:
        return None
    return re.sub(r"[^a-z0-9.]+", "-", base.lower()).strip("-"), base


def family_of(slug: str) -> str:
    return re.sub(r"-\d+(?:\.\d+)*$", "", slug)


# ── storage ──────────────────────────────────────────────────────────────────

def db_path(root: Path) -> Path:
    p = Path(root) / "data" / DB_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _connect(root: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(root), timeout=15)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def known_families(root: Path, cfg: dict) -> list[str]:
    with _LOCK, _connect(root) as con:
        found = [r[0] for r in con.execute("SELECT DISTINCT family FROM candidates WHERE family != ''")]
    return sorted(set(cfg.get("families", [])) | set(found))


def add_signal(root: Path, sig: dict, *, families: list[str], allow_new_family: bool = False) -> str:
    """Store one observation; returns the candidate slug it landed on ('' if none).

    A signal already stored (same source + ext_id) only has its metrics refreshed:
    the score of a Reddit thread grows, its time of posting does not.
    """
    title = sig.get("title") or ""
    m = match(title + " " + (sig.get("match_text") or ""), families)
    if m is None and allow_new_family and sig.get("repo_id"):
        guess = name_from_repo(sig["repo_id"])
        if guess:
            m = (guess[0], guess[1], family_of(guess[0]))
    slug = m[0] if m else ""
    now = int(time.time())
    ts = int(sig.get("ts") or now)
    metrics = json.dumps(sig.get("metrics") or {})
    with _LOCK, _connect(root) as con:
        cur = con.execute("SELECT id, candidate FROM signals WHERE source=? AND ext_id=?",
                          (sig["source"], str(sig["ext_id"])))
        row = cur.fetchone()
        if row:
            con.execute("UPDATE signals SET metrics=?, seen=? WHERE id=?", (metrics, now, row["id"]))
            slug = row["candidate"] or slug
        else:
            con.execute(
                "INSERT INTO signals (source, ext_id, kind, title, url, author, ts, seen, metrics, candidate)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sig["source"], str(sig["ext_id"]), sig["kind"], title[:400],
                 (sig.get("url") or "")[:600], (sig.get("author") or "")[:120], ts, now,
                 metrics, slug))
        if slug and m:
            con.execute(
                "INSERT INTO candidates (slug, name, family, first_ts, updated) VALUES (?,?,?,?,?)"
                " ON CONFLICT(slug) DO UPDATE SET first_ts=MIN(first_ts, excluded.first_ts)",
                (slug, m[1], m[2], ts, now))
    return slug


def signals_for(root: Path, slug: str) -> list[dict]:
    with _LOCK, _connect(root) as con:
        rows = con.execute("SELECT * FROM signals WHERE candidate=? ORDER BY ts", (slug,)).fetchall()
    return [_sig_row(r) for r in rows]


def recent_signals(root: Path, limit: int = 100, kind: str = "") -> list[dict]:
    q, args = "SELECT * FROM signals", []
    if kind:
        q += " WHERE kind=?"
        args.append(kind)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(int(limit))
    with _LOCK, _connect(root) as con:
        rows = con.execute(q, args).fetchall()
    return [_sig_row(r) for r in rows]


def _sig_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["metrics"] = json.loads(d.get("metrics") or "{}")
    except ValueError:
        d["metrics"] = {}
    return d


def candidates(root: Path, *, include_dismissed: bool = False, limit: int = 200) -> list[dict]:
    q = "SELECT * FROM candidates"
    if not include_dismissed:
        q += " WHERE status != 'dismissed'"
    q += " ORDER BY status='pinned' DESC, hot DESC LIMIT ?"
    with _LOCK, _connect(root) as con:
        rows = con.execute(q, (int(limit),)).fetchall()
        counts = dict(con.execute("SELECT candidate, COUNT(*) FROM signals GROUP BY candidate").fetchall())
    out = []
    for r in rows:
        d = dict(r)
        d["score"] = json.loads(d.get("score") or "{}")
        d["signal_count"] = counts.get(d["slug"], 0)
        out.append(d)
    return out


def get_candidate(root: Path, slug: str) -> dict | None:
    with _LOCK, _connect(root) as con:
        r = con.execute("SELECT * FROM candidates WHERE slug=?", (slug,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["score"] = json.loads(d.get("score") or "{}")
    return d


def set_status(root: Path, slug: str, status: str, notes: str | None = None) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with _LOCK, _connect(root) as con:
        n = con.execute("UPDATE candidates SET status=?, notes=COALESCE(?, notes) WHERE slug=?",
                        (status, notes, slug)).rowcount
    if not n:
        raise KeyError(slug)
    return get_candidate(root, slug) or {}


def merge(root: Path, src: str, dst: str) -> int:
    """Fold one candidate into another (the matcher split "LTX-2" and "LTXV 2")."""
    if src == dst:
        return 0
    with _LOCK, _connect(root) as con:
        if not con.execute("SELECT 1 FROM candidates WHERE slug=?", (dst,)).fetchone():
            raise KeyError(dst)
        n = con.execute("UPDATE signals SET candidate=? WHERE candidate=?", (dst, src)).rowcount
        con.execute("DELETE FROM candidates WHERE slug=?", (src,))
    return n


def start_run(root: Path) -> int:
    with _LOCK, _connect(root) as con:
        return int(con.execute("INSERT INTO runs (started) VALUES (?)", (int(time.time()),)).lastrowid)


def finish_run(root: Path, run_id: int, ok: bool, detail: dict) -> None:
    with _LOCK, _connect(root) as con:
        con.execute("UPDATE runs SET finished=?, ok=?, detail=? WHERE id=?",
                    (int(time.time()), int(ok), json.dumps(detail)[:20000], run_id))


def runs(root: Path, limit: int = 20) -> list[dict]:
    with _LOCK, _connect(root) as con:
        rows = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.get("detail") or "{}")
        out.append(d)
    return out


def add_benchmark(root: Path, slug: str, template: str) -> int:
    with _LOCK, _connect(root) as con:
        return int(con.execute("INSERT INTO benchmarks (slug, template, started) VALUES (?,?,?)",
                               (slug, template, int(time.time()))).lastrowid)


def finish_benchmark(root: Path, bid: int, status: str, *, seconds: float | None = None,
                     vram_peak: int | None = None, detail: str = "") -> None:
    with _LOCK, _connect(root) as con:
        con.execute("UPDATE benchmarks SET finished=?, status=?, seconds=?, vram_peak=?, detail=? WHERE id=?",
                    (int(time.time()), status, seconds, vram_peak, detail[:2000], bid))


def benchmarks(root: Path, slug: str = "", limit: int = 50) -> list[dict]:
    q, args = "SELECT * FROM benchmarks", []
    if slug:
        q += " WHERE slug=?"
        args.append(slug)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with _LOCK, _connect(root) as con:
        return [dict(r) for r in con.execute(q, args).fetchall()]


# ── scoring ──────────────────────────────────────────────────────────────────

_PARAMS = re.compile(r"(?<![\w.])a?(\d{1,3}(?:\.\d)?)\s?b(?![a-z])", re.I)
_VIDEO_WORDS = ("video", "t2v", "i2v", "ti2v", "animate", "motion", "talking", "avatar")


def _sat(x: float, k: float) -> float:
    """0..1 saturation curve: k is the input that gets you to ~63 %."""
    return 1 - math.exp(-max(x, 0) / k) if k > 0 else 0.0


def _has(text: str, words: list[str]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def score(signals: list[dict], now: int, cfg: dict, *, family: str = "",
          include_detail: bool = True, catches: int = 0) -> dict:
    """Score one candidate from its signals as of ``now``.

    Only signals with ``ts <= now`` count, which is what makes the same function
    the backtest: replaying history is calling this with an earlier ``now``.
    """
    sigs = [s for s in signals if int(s.get("ts") or 0) <= now]
    if not sigs:
        return {"hot": 0.0, "stage": "rumor", "action": "WATCH", "parts": {}}

    stage_rank = max((STAGES.index(STAGE_OF_KIND[s["kind"]]) for s in sigs
                      if s["kind"] in STAGE_OF_KIND), default=0)
    stage = STAGES[stage_rank]
    stage_c = STAGE_VALUE[stage]

    def weighted(lo: int, hi: int) -> float:
        return sum(KIND_WEIGHT.get(s["kind"], 1.0) for s in sigs if lo < int(s["ts"]) <= hi)

    day = 86400
    recent, prior = weighted(now - 2 * day, now), weighted(now - 4 * day, now - 2 * day)
    momentum_c = _sat(recent, 6.0) * (1.0 if recent >= prior else 0.75)

    hype = cfg.get("hype_words", [])
    buzz_raw, hyped, threads = 0.0, 0, 0
    for s in sigs:
        if s["kind"] != "buzz" or int(s["ts"]) < now - 7 * day:
            continue
        mt = s.get("metrics") or {}
        threads += 1
        buzz_raw += math.log1p(max(mt.get("score") or 0, 0)) + 0.5 * math.log1p(max(mt.get("comments") or 0, 0))
        if not mt.get("score"):
            buzz_raw += 1.0                     # anonymous feeds carry no score; a thread still counts
        if _has(s.get("title", "") + " " + (mt.get("text") or ""), hype):
            hyped += 1
    excitement = hyped / threads if threads else 0.0
    buzz_c = min(1.0, _sat(buzz_raw, 15.0) * (0.7 + 0.6 * excitement))

    coverage = [s for s in sigs if s["kind"] in ("video", "short") and int(s["ts"]) >= now - 14 * day]
    best_vpd = 0.0
    for s in coverage:
        mt = s.get("metrics") or {}
        age_d = max((now - int(s["ts"])) / day, 0.5)
        if mt.get("views"):
            best_vpd = max(best_vpd, mt["views"] / age_d)
    likes = max([(s.get("metrics") or {}).get("likes") or 0 for s in sigs
                 if s["kind"] in ("weights", "gguf", "repo")] or [0])
    demand_c = max(_sat(math.log1p(best_vpd), 8.0), _sat(math.log1p(likes), 6.0))

    text = " ".join((s.get("title") or "") + " " + " ".join((s.get("metrics") or {}).get("tags") or [])
                    for s in sigs).lower()
    fit = 0.35
    if any(w in text for w in _VIDEO_WORDS):
        fit += 0.3
    if any(s["kind"] == "gguf" for s in sigs) or "gguf" in text:
        fit += 0.2
    sizes = [float(x) for x in _PARAMS.findall(text)]
    if sizes:
        smallest = min(sizes)
        fit += 0.15 if smallest <= 14 else (-0.25 if smallest > 30 else 0.0)
    if any(s["kind"] == "code" and "comfy" in (s.get("source") or "").lower() for s in sigs):
        fit += 0.1
    if family and family in [f.lower() for f in cfg.get("closed_families", [])]:
        fit -= 0.45
    fit_c = min(max(fit, 0.0), 1.0)

    recent_cov = [s for s in coverage if int(s["ts"]) >= now - 10 * day and s["kind"] == "video"]
    lowvram = [s for s in recent_cov if _has(s.get("title", ""), cfg.get("lowvram_words", []))]
    saturation = _sat(len(recent_cov), 4.0)
    lowvram_sat = _sat(len(lowvram), 2.0)
    # For a low-VRAM channel the 4 GB angle is what is scarce: generic coverage
    # only costs half as much while nobody has done a low-VRAM take.
    sat_c = saturation * (0.5 + 0.5 * lowvram_sat)

    w = {**DEFAULT_CONFIG["weights"], **(cfg.get("weights") or {})}
    pos = w["stage"] + w["momentum"] + w["buzz"] + w["demand"] + w["fit"]
    raw = (w["stage"] * stage_c + w["momentum"] * momentum_c + w["buzz"] * buzz_c +
           w["demand"] * demand_c + w["fit"] * fit_c - w["saturation"] * sat_c)
    hot = 100.0 * max(raw, 0.0) / pos if pos else 0.0

    # Caught more than once counts. Independent source types confirming the same
    # model (a PR *and* a Reddit thread *and* an HF repo) are worth more than ten
    # posts in one subreddit, and an idea the studio proposed on several runs is
    # one it keeps arriving at for a reason.
    families_seen = {(s.get("source") or "").split(":")[0] for s in sigs
                     if s["kind"] not in COVERAGE_KINDS}
    confirm_bonus = min(8.0, 2.5 * max(len(families_seen) - 1, 0))
    catch_bonus = min(12.0, 4.0 * max(int(catches) - 1, 0))
    hot += confirm_bonus + catch_bonus

    last = max(int(s["ts"]) for s in sigs)
    idle_days = (now - last) / day
    if idle_days > 21:
        hot *= math.exp(-(idle_days - 21) / 14)
    hot = round(min(hot, 100.0), 1)

    own = any(s["kind"] == "own" for s in sigs)
    if own:
        action = "DONE"
    elif hot < 40:
        action = "WATCH"
    elif stage in ("paper", "repo", "code"):
        action = "PREP NOW"
    elif stage in ("weights", "gguf"):
        if saturation < 0.5:
            action = "SHOOT NOW"
        elif lowvram_sat < 0.5:
            action = "SHOOT: 4GB ANGLE"
        else:
            action = "LATE"
    else:
        action = "WATCH"

    out = {"hot": hot, "stage": stage, "action": action}
    if include_detail:
        release = min((int(s["ts"]) for s in sigs if s["kind"] in ("weights", "gguf")), default=None)
        out["parts"] = {
            "stage": round(stage_c, 3), "momentum": round(momentum_c, 3), "buzz": round(buzz_c, 3),
            "excitement": round(excitement, 3), "demand": round(demand_c, 3), "fit": round(fit_c, 3),
            "saturation": round(saturation, 3), "lowvram_saturation": round(lowvram_sat, 3),
            "creator_videos_10d": len(recent_cov), "lowvram_videos_10d": len(lowvram),
            "threads_7d": threads, "release_ts": release,
            "best_views_per_day": round(best_vpd),
            "sources_confirming": len(families_seen), "confirm_bonus": round(confirm_bonus, 1),
            "idea_catches": int(catches), "catch_bonus": round(catch_bonus, 1),
        }
    return out


def rescore(root: Path, cfg: dict, now: int | None = None) -> list[dict]:
    """Recompute every candidate's score; returns the ones that newly crossed the
    alert line (each alerts once, so a schedule every 3 h does not nag)."""
    now = int(now or time.time())
    threshold = float(cfg.get("alert_threshold", 70))
    fresh_alerts: list[dict] = []
    with _LOCK, _connect(root) as con:
        cands = [dict(r) for r in con.execute("SELECT slug, family, alerted, status, name FROM candidates")]
        catches = dict(con.execute("SELECT candidate, MAX(catches) FROM ideas WHERE status != 'archived'"
                                   " GROUP BY candidate").fetchall())
        for c in cands:
            sigs = [_sig_row(r) for r in con.execute(
                "SELECT * FROM signals WHERE candidate=?", (c["slug"],)).fetchall()]
            sc = score(sigs, now, cfg, family=c["family"], catches=catches.get(c["slug"], 0))
            if any(s["kind"] == "own" for s in sigs) and c["status"] == "watch":
                c["status"] = "covered"
            alerted = c["alerted"]
            if sc["hot"] >= threshold and not alerted and c["status"] in ("watch", "pinned"):
                alerted = now
                fresh_alerts.append({"slug": c["slug"], "name": c["name"], **sc})
            con.execute("UPDATE candidates SET hot=?, stage=?, action=?, score=?, alerted=?, status=?,"
                        " updated=? WHERE slug=?",
                        (sc["hot"], sc["stage"], sc["action"], json.dumps(sc.get("parts", {})),
                         alerted, c["status"], now, c["slug"]))
            con.execute("UPDATE ideas SET hot=? WHERE candidate=? AND status != 'archived'",
                        (sc["hot"], c["slug"]))
    archive_stale_ideas(root, now)
    return fresh_alerts


# ── backtest ─────────────────────────────────────────────────────────────────

def backtest(signals: list[dict], cfg: dict, *, family: str = "", step_hours: int = 6,
             start: int | None = None, end: int | None = None) -> dict:
    """Replay one candidate's history and report when the radar would have fired.

    The lead times that matter for the channel:
      - alert -> weights: how much prep time the alert buys;
      - alert -> first competitor video: whether you would have been first;
      - own upload vs. the competitor uploads: your rank in hindsight.
    """
    if not signals:
        return {"ok": False, "error": "no signals"}
    ts_all = sorted(int(s["ts"]) for s in signals)
    start = int(start or ts_all[0] - 86400)
    end = int(end or ts_all[-1] + 86400)
    threshold = float(cfg.get("alert_threshold", 70))
    step = max(int(step_hours), 1) * 3600

    series, alert_ts, prep_ts = [], None, None
    t = start
    while t <= end and len(series) < 4000:
        sc = score(signals, t, cfg, family=family, include_detail=False)
        series.append({"t": t, "hot": sc["hot"], "stage": sc["stage"]})
        if alert_ts is None and sc["hot"] >= threshold:
            alert_ts = t
        if prep_ts is None and sc["action"] in ("PREP NOW", "SHOOT NOW", "SHOOT: 4GB ANGLE"):
            prep_ts = t
        t += step

    def first(kinds: tuple[str, ...]) -> int | None:
        return min((int(s["ts"]) for s in signals if s["kind"] in kinds), default=None)

    release = first(("weights", "gguf"))
    first_video = first(("video",))
    own = first(("own",))
    competitor_before_own = (sum(1 for s in signals if s["kind"] == "video" and own and int(s["ts"]) < own)
                             if own else None)

    def hours(a: int | None, b: int | None) -> float | None:
        return round((b - a) / 3600, 1) if a is not None and b is not None else None

    return {
        "ok": True,
        "threshold": threshold,
        "first_signal": ts_all[0],
        "alert": alert_ts,
        "actionable": prep_ts,
        "release": release,
        "first_competitor_video": first_video,
        "own_upload": own,
        "lead_alert_to_release_h": hours(alert_ts, release),
        "lead_alert_to_first_video_h": hours(alert_ts, first_video),
        "own_after_release_h": hours(release, own),
        "own_rank": (competitor_before_own + 1) if own else None,
        "caught_before_release": bool(alert_ts and release and alert_ts <= release),
        "series": series,
    }


def backtest_all(root: Path, cfg: dict) -> dict:
    """Backtest every candidate with a release; recall is over the ones you covered."""
    rows = []
    for c in candidates(root, include_dismissed=True, limit=1000):
        sigs = signals_for(root, c["slug"])
        if not any(s["kind"] in ("weights", "gguf", "own") for s in sigs):
            continue
        r = backtest(sigs, cfg, family=c["family"])
        r.pop("series", None)
        rows.append({"slug": c["slug"], "name": c["name"], **r})
    covered = [r for r in rows if r.get("own_upload")]
    caught = [r for r in covered if r.get("alert") and r["alert"] <= (r["own_upload"] or 0)]
    alerted = [r for r in rows if r.get("alert")]
    real = [r for r in alerted if r.get("release")]
    leads = sorted(r["lead_alert_to_release_h"] for r in rows
                   if r.get("lead_alert_to_release_h") is not None)
    return {
        "rows": rows,
        "summary": {
            "candidates_with_release": len(rows),
            "covered_by_you": len(covered),
            "recall_on_your_videos": round(len(caught) / len(covered), 3) if covered else None,
            "precision_alert_became_release": round(len(real) / len(alerted), 3) if alerted else None,
            "median_lead_alert_to_release_h": leads[len(leads) // 2] if leads else None,
        },
    }


# ── ideas (written by the Idea Studio agent, or by you over MCP) ─────────────

IDEA_STATUSES = ("new", "shortlisted", "producing", "published", "archived")
_STOP = {"the", "a", "an", "on", "in", "of", "to", "and", "or", "for", "with", "my", "your",
         "it", "is", "this", "that", "i", "you", "vs", "how", "what", "can", "will", "does",
         "run", "runs", "really", "actually", "test", "tested", "new"}


def _tokens(text: str, ignore: set[str] | None = None) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9.]+", (text or "").lower()):
        t = t.strip(".")
        if t and len(t) > 1 and t not in _STOP and t not in (ignore or set()):
            out.add(t)
    return out


def idea_similarity(a: dict, b: dict) -> float:
    """How likely two ideas on the same model are the same video, 0..1.

    The model's own name is ignored (every idea on Wan 2.5 says "Wan 2.5"). The
    angle is the strongest evidence: "4GB benchmark" and "4GB benchmark day one"
    are one video however differently the titles are phrased, so angle
    containment counts on its own. Otherwise title + angle words are compared.
    """
    slug = a.get("candidate") or b.get("candidate") or ""
    ignore = set(re.split(r"[-_]", slug.lower())) if slug else set()
    aa, ab = _tokens(a.get("angle", ""), ignore), _tokens(b.get("angle", ""), ignore)
    angle = len(aa & ab) / min(len(aa), len(ab)) if aa and ab and min(len(aa), len(ab)) >= 2 else 0.0
    ta = _tokens(f"{a.get('title', '')} {a.get('angle', '')}", ignore)
    tb = _tokens(f"{b.get('title', '')} {b.get('angle', '')}", ignore)
    words = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(words, 0.9 * angle)


MERGE_AT = 0.45


def _idea_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("alt_titles", "evidence"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except ValueError:
            d[k] = []
    return d


def save_idea(root: Path, idea: dict) -> dict:
    """Store an idea, or fold it into the existing one it duplicates.

    A duplicate is not thrown away: its title joins the alternatives, empty
    fields are filled from it, and ``catches`` goes up, which feeds the
    candidate's hot score on the next rescore. Returns the stored idea plus
    ``merged`` (bool).
    """
    title = (idea.get("title") or "").strip()
    if not title:
        raise ValueError("an idea needs a title")
    svg = idea.get("thumb_svg") or ""
    if svg and not svg.lstrip().startswith("<svg"):
        raise ValueError("thumb_svg must be a single <svg> element")
    if len(svg) > 200_000:
        raise ValueError("thumb_svg is over 200 KB")
    now = int(time.time())
    slug = (idea.get("candidate") or "").strip()
    with _LOCK, _connect(root) as con:
        if slug and not con.execute("SELECT 1 FROM candidates WHERE slug=?", (slug,)).fetchone():
            raise KeyError(f"no candidate {slug!r} (see trend_radar_list)")
        rows = [_idea_row(r) for r in con.execute(
            "SELECT * FROM ideas WHERE candidate=? AND status != 'archived'", (slug,)).fetchall()]
        twin = max(rows, key=lambda r: idea_similarity(r, idea), default=None)
        if twin and idea_similarity(twin, idea) >= MERGE_AT:
            alts = list(dict.fromkeys(twin["alt_titles"] + [title] + list(idea.get("alt_titles") or [])))
            alts = [t for t in alts if t != twin["title"]][:12]
            evidence = list(dict.fromkeys(twin["evidence"] + list(idea.get("evidence") or [])))[:30]
            fields = {k: (twin.get(k) or idea.get(k) or "") for k in
                      ("angle", "hook", "description", "script", "thumb_svg", "thumb_text",
                       "thumb_notes", "why_now", "research")}
            # A newer draft of the long fields wins when the studio rewrites them.
            for k in ("description", "script", "thumb_svg", "research", "why_now"):
                if idea.get(k):
                    fields[k] = idea[k]
            con.execute(
                "UPDATE ideas SET alt_titles=?, evidence=?, angle=?, hook=?, description=?, script=?,"
                " thumb_svg=?, thumb_text=?, thumb_notes=?, why_now=?, research=?, catches=catches+1,"
                " updated=? WHERE id=?",
                (json.dumps(alts), json.dumps(evidence), fields["angle"], fields["hook"],
                 fields["description"], fields["script"], fields["thumb_svg"], fields["thumb_text"],
                 fields["thumb_notes"], fields["why_now"], fields["research"], now, twin["id"]))
            iid, merged = twin["id"], True
        else:
            hot = con.execute("SELECT hot FROM candidates WHERE slug=?", (slug,)).fetchone()
            iid = con.execute(
                "INSERT INTO ideas (candidate, title, alt_titles, angle, hook, description, script,"
                " thumb_svg, thumb_text, thumb_notes, why_now, evidence, research, hot, created, updated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (slug, title[:200], json.dumps(list(idea.get("alt_titles") or [])[:12]),
                 (idea.get("angle") or "")[:400], (idea.get("hook") or "")[:1000],
                 (idea.get("description") or "")[:8000], (idea.get("script") or "")[:40000],
                 svg, (idea.get("thumb_text") or "")[:120], (idea.get("thumb_notes") or "")[:2000],
                 (idea.get("why_now") or "")[:2000], json.dumps(list(idea.get("evidence") or [])[:30]),
                 (idea.get("research") or "")[:12000], float(hot[0]) if hot else 0.0, now, now)).lastrowid
            merged = False
    out = get_idea(root, int(iid)) or {}
    out["merged"] = merged
    return out


def get_idea(root: Path, iid: int) -> dict | None:
    with _LOCK, _connect(root) as con:
        r = con.execute("SELECT * FROM ideas WHERE id=?", (int(iid),)).fetchone()
    return _idea_row(r) if r else None


def ideas(root: Path, *, status: str = "", candidate: str = "", include_archived: bool = False,
          limit: int = 100, full: bool = False) -> list[dict]:
    cols = "*" if full else ("id, candidate, title, alt_titles, angle, hook, thumb_text, why_now,"
                             " status, archive_reason, catches, hot, created, updated,"
                             " length(thumb_svg) > 0 AS has_thumb")
    q, args = f"SELECT {cols} FROM ideas WHERE 1=1", []
    if status:
        q += " AND status=?"
        args.append(status)
    elif not include_archived:
        q += " AND status != 'archived'"
    if candidate:
        q += " AND candidate=?"
        args.append(candidate)
    q += " ORDER BY (status='shortlisted') DESC, hot + 4 * (catches - 1) DESC, updated DESC LIMIT ?"
    args.append(int(limit))
    with _LOCK, _connect(root) as con:
        rows = con.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["alt_titles"] = json.loads(d.get("alt_titles") or "[]")
        except ValueError:
            d["alt_titles"] = []
        if full:
            try:
                d["evidence"] = json.loads(d.get("evidence") or "[]")
            except ValueError:
                d["evidence"] = []
        out.append(d)
    return out


def set_idea(root: Path, iid: int, updates: dict) -> dict:
    """Edit an idea by hand (title, status, script...). Archiving needs a reason."""
    allowed = {"title", "angle", "hook", "description", "script", "thumb_text", "thumb_notes",
               "why_now", "status", "archive_reason", "thumb_svg"}
    bad = set(updates) - allowed
    if bad:
        raise ValueError(f"cannot set {sorted(bad)}")
    if "status" in updates and updates["status"] not in IDEA_STATUSES:
        raise ValueError(f"status must be one of {IDEA_STATUSES}")
    if updates.get("status") == "archived" and not updates.get("archive_reason"):
        updates["archive_reason"] = "archived by hand"
    if updates.get("status") and updates["status"] not in ("archived", "published"):
        updates["archive_reason"] = ""
    if not updates:
        return get_idea(root, iid) or {}
    sets = ", ".join(f"{k}=?" for k in updates)
    with _LOCK, _connect(root) as con:
        n = con.execute(f"UPDATE ideas SET {sets}, updated=? WHERE id=?",
                        (*updates.values(), int(time.time()), int(iid))).rowcount
    if not n:
        raise KeyError(iid)
    return get_idea(root, iid) or {}


def archive_stale_ideas(root: Path, now: int | None = None) -> int:
    """Archive ideas that stopped making sense, with the reason written down.

    - its model was dismissed;
    - the window closed (action LATE: covered widely, including low-VRAM);
    - untouched for 21 days and never shortlisted;
    - its model went cold (hot < 20) for a week.
    Shortlisted / producing / published ideas are never auto-archived.
    """
    now = int(now or time.time())
    day = 86400
    n = 0
    with _LOCK, _connect(root) as con:
        rows = con.execute(
            "SELECT i.id, i.updated, c.status AS cstatus, c.action, c.hot, c.updated AS cupd"
            " FROM ideas i LEFT JOIN candidates c ON c.slug = i.candidate"
            " WHERE i.status = 'new'").fetchall()
        for r in rows:
            reason = ""
            if r["cstatus"] == "dismissed":
                reason = "model dismissed"
            elif r["action"] == "LATE":
                reason = "window closed: widely covered, low-VRAM angle taken too"
            elif now - int(r["updated"]) > 21 * day:
                reason = "stale: untouched for 3 weeks"
            elif r["hot"] is not None and r["hot"] < 20 and now - int(r["updated"]) > 7 * day:
                reason = "model went cold"
            if reason:
                con.execute("UPDATE ideas SET status='archived', archive_reason=?, updated=? WHERE id=?",
                            (reason, now, r["id"]))
                n += 1
    return n


# ── Idea Studio workspace (skills + knowledge the agent reads) ───────────────

STUDIO_DIR = "trend_radar_studio"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def studio_dir(root: Path) -> Path:
    p = Path(root) / "data" / STUDIO_DIR
    (p / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    (p / "knowledge").mkdir(parents=True, exist_ok=True)
    return p


def list_skills(root: Path) -> list[dict]:
    base = studio_dir(root) / ".claude" / "skills"
    out = []
    for d in sorted(base.iterdir()):
        sk = d / "SKILL.md"
        if d.is_dir() and sk.exists():
            text = sk.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^description:\s*(.+)$", text, re.M)
            files = sum(1 for f in d.rglob("*") if f.is_file())
            out.append({"name": d.name, "description": (m.group(1).strip() if m else "")[:300],
                        "files": files, "bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file())})
    return out


def install_skill(root: Path, name: str, files: dict[str, bytes]) -> dict:
    """Write a skill folder. ``files`` maps relative paths to bytes; must hold SKILL.md.

    Paths are confined to the skill folder (no ``..``, no absolute paths), and the
    folder is replaced whole so a re-upload cannot leave stale reference files.
    """
    import shutil

    name = name.strip().lower()
    if not _SAFE_NAME.match(name):
        raise ValueError("skill name: lowercase letters, digits, - and _ only")
    if "SKILL.md" not in files:
        raise ValueError("a skill needs a SKILL.md at its top level")
    if len(files) > 200 or sum(len(b) for b in files.values()) > 20 * 1024 * 1024:
        raise ValueError("skill is too large (200 files / 20 MB max)")
    base = studio_dir(root) / ".claude" / "skills"
    target = base / name
    staging = base / f".{name}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    for rel, data in files.items():
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise ValueError(f"unsafe path in skill: {rel}")
        dest = staging / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    return next(s for s in list_skills(root) if s["name"] == name)


def remove_skill(root: Path, name: str) -> bool:
    import shutil

    if not _SAFE_NAME.match(name or ""):
        return False
    target = studio_dir(root) / ".claude" / "skills" / name
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def list_knowledge(root: Path) -> list[dict]:
    base = studio_dir(root) / "knowledge"
    return [{"name": f.name, "bytes": f.stat().st_size} for f in sorted(base.iterdir()) if f.is_file()]


def save_knowledge(root: Path, name: str, data: bytes) -> dict:
    clean = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip() or "note.md"
    if not clean.lower().endswith((".md", ".txt", ".csv", ".json")):
        raise ValueError("knowledge files: .md .txt .csv .json")
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("knowledge file over 5 MB")
    (studio_dir(root) / "knowledge" / clean).write_bytes(data)
    return {"name": clean, "bytes": len(data)}


def remove_knowledge(root: Path, name: str) -> bool:
    f = studio_dir(root) / "knowledge" / Path(name).name
    if f.is_file():
        f.unlink()
        return True
    return False


DEFAULT_STUDIO_BRIEF = """\
# Idea Studio: the_frizzy1

Channel: local, free AI video/image generation tested honestly on weak hardware
("Will It Run On 4GB?"). Speed matters: the channel wins when it is first, and the
low-VRAM angle is the one bigger channels skip.

Rules that never bend:
- Never invent benchmark numbers. Use [RESULT] placeholders until a real run exists.
- No em dashes in titles, descriptions or scripts. Use commas or colons.
- Calm and honest, never accusatory about other guides.
- Skills in .claude/skills are the source of truth for voice, titles and thumbnails.
- Files in knowledge/ are background (analytics exports, past scripts, notes). Read
  what is relevant before writing.
"""


def studio_prompt(cfg: dict) -> str:
    tpl = (cfg.get("studio_prompt") or "").strip() or DEFAULT_STUDIO_PROMPT
    return tpl.replace("{max_candidates}", str(cfg.get("studio_max_candidates", 4)))


def studio_brief_path(root: Path) -> Path:
    return studio_dir(root) / "CLAUDE.md"


def load_studio_brief(root: Path) -> str:
    p = studio_brief_path(root)
    return p.read_text(encoding="utf-8") if p.exists() else DEFAULT_STUDIO_BRIEF


def save_studio_brief(root: Path, text: str) -> None:
    if len(text) > 100_000:
        raise ValueError("brief over 100 KB")
    studio_brief_path(root).write_text(text, encoding="utf-8")


DEFAULT_STUDIO_PROMPT = """\
Daily Idea Studio run for the YouTube channel the_frizzy1. Work through Plutus's
trend_radar_* tools. Read CLAUDE.md, your skills in .claude/skills and anything
relevant in knowledge/ first.

1. trend_radar_ideas: see what already exists so you extend rather than repeat.
2. trend_radar_list: take the candidates whose action is PREP NOW, SHOOT NOW or
   SHOOT: 4GB ANGLE (at most {max_candidates}).
3. For each: trend_radar_candidate (the evidence), then trend_radar_title_research
   with the model name to see what titles and thumbnails other creators used and
   which ones over-performed (views per day vs. channel size). Note the patterns.
4. Write one idea per strong angle with trend_radar_save_idea: title (+2-3
   alternatives), angle, hook (first 15 seconds), why_now, a description following
   the channel template, a script outline, thumbnail text and a 1280x720 SVG
   thumbnail mock following the thumbnail skill, plus evidence URLs and a short
   research note on what worked for others. Saving an idea that matches an
   existing one on the same model merges it and counts as another catch, which is
   intended: do it when you independently arrive at the same video.
5. trend_radar_set_status to archive ideas that no longer make sense (reason!).

Never invent benchmark numbers; use [RESULT]. Finish with a 5-line summary of what
you added, merged and archived.
"""


# ── "looks finished": cross-reference ideas with your own uploads ────────────

DONE_MATCH_AT = 0.3


def suggest_done(root: Path) -> dict:
    """Ideas and models your own channel already covered, for one-click cleanup.

    An idea is suggested as done when one of your uploads (``own`` signals, from
    ``my_channel``) is about the same model and was published after the idea was
    first written, or when its title clearly overlaps the idea's title/angle.
    Nothing changes until ``apply_done`` is called.
    """
    with _LOCK, _connect(root) as con:
        own = [_sig_row(r) for r in con.execute(
            "SELECT * FROM signals WHERE kind='own' ORDER BY ts DESC").fetchall()]
        open_ideas = [_idea_row(r) for r in con.execute(
            "SELECT * FROM ideas WHERE status IN ('new','shortlisted','producing')").fetchall()]
        cands = [dict(r) for r in con.execute(
            "SELECT slug, name, status FROM candidates WHERE status IN ('watch','pinned')").fetchall()]
    ideas_out = []
    for i in open_ideas:
        best, best_sim, why = None, 0.0, ""
        for v in own:
            same_model = bool(i["candidate"]) and v["candidate"] == i["candidate"]
            sim = idea_similarity({"title": v["title"]}, i)
            if same_model and int(v["ts"]) >= int(i["created"]) - 3 * 86400:
                score_ = 1.0 + sim
                reason = "your upload on the same model, after the idea"
            elif sim >= DONE_MATCH_AT:
                score_ = sim
                reason = f"title overlap {sim:.0%}"
            else:
                continue
            if score_ > best_sim:
                best, best_sim, why = v, score_, reason
        if best:
            ideas_out.append({"idea_id": i["id"], "idea_title": i["title"], "candidate": i["candidate"],
                              "video_title": best["title"], "video_url": best["url"],
                              "video_ts": best["ts"], "reason": why})
    covered = {v["candidate"] for v in own if v["candidate"]}
    cands_out = [{"slug": c["slug"], "name": c["name"],
                  "video": next((v["title"] for v in own if v["candidate"] == c["slug"]), "")}
                 for c in cands if c["slug"] in covered]
    return {"ideas": ideas_out, "candidates": cands_out, "own_videos_seen": len(own)}


def apply_done(root: Path, idea_ids: list[int] | None = None, slugs: list[str] | None = None) -> dict:
    """Mark suggested ideas published and models covered (all suggestions when
    both lists are None). Only items that are still suggested get changed."""
    sug = suggest_done(root)
    want_i = None if idea_ids is None else {int(x) for x in idea_ids}
    want_c = None if slugs is None else set(slugs)
    now = int(time.time())
    n_i = n_c = 0
    with _LOCK, _connect(root) as con:
        for s in sug["ideas"]:
            if want_i is None or s["idea_id"] in want_i:
                con.execute("UPDATE ideas SET status='published', archive_reason=?, updated=? WHERE id=?",
                            (f"done: {s['video_title'][:200]}", now, s["idea_id"]))
                n_i += 1
        for c in sug["candidates"]:
            if want_c is None or c["slug"] in want_c:
                con.execute("UPDATE candidates SET status='covered' WHERE slug=?", (c["slug"],))
                n_c += 1
    return {"ideas": n_i, "candidates": n_c}


def finished(root: Path, limit: int = 200) -> dict:
    """The Finished drawer: published ideas and covered models, newest first."""
    with _LOCK, _connect(root) as con:
        ideas_ = [_idea_row(r) for r in con.execute(
            "SELECT id, candidate, title, alt_titles, evidence, status, archive_reason, catches, hot,"
            " created, updated, length(thumb_svg) > 0 AS has_thumb FROM ideas"
            " WHERE status='published' ORDER BY updated DESC LIMIT ?", (int(limit),)).fetchall()]
        cands = [dict(r) for r in con.execute(
            "SELECT slug, name, hot, stage, updated FROM candidates WHERE status='covered'"
            " ORDER BY updated DESC LIMIT ?", (int(limit),)).fetchall()]
    return {"ideas": ideas_, "candidates": cands}
