"""Tracked YouTube channels and their numbers over time.

A channel's subscriber count today says very little; the same count next to last
week's says whether it is growing. So tracking is snapshots: every run of
``youtube_track`` (action "snapshot") stores the channel's totals and the view
counts of its recent uploads, and the report is plain subtraction between two of
them. Nothing is estimated — a delta is only shown between two readings that
were actually taken.

"New upload" needs care without publish dates (no API key): a video first seen
in a later snapshot may simply be an older one that a larger capture reached.
So each reading keeps the video's position in the channel's newest-first list,
and an undated video counts as new only when it appeared *above* every video
already known — which is what a fresh upload does and a back-catalogue one
cannot. Each channel also remembers its capture size, so snapshots stay
comparable unless someone deliberately changes it.

Each snapshot records where its numbers came from. The Data API returns exact
view counts; the public channel page rounds them ("11K"), and a delta between a
rounded and an exact reading would be noise presented as growth, so the report
says when a window mixes the two.

SQLite in Plutus's own data directory, like ``core/agent_db.py``: no
credentials, no network, nothing to configure.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

DB_FILE = "youtube.sqlite3"
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    handle     TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    added_at   INTEGER NOT NULL,
    max_videos INTEGER NOT NULL DEFAULT 30
);
CREATE TABLE IF NOT EXISTS channel_snapshots (
    channel_id  TEXT NOT NULL,
    taken_at    INTEGER NOT NULL,
    subscribers INTEGER,
    views       INTEGER,
    videos      INTEGER,
    source      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (channel_id, taken_at)
);
CREATE TABLE IF NOT EXISTS videos (
    video_id   TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    published  TEXT NOT NULL DEFAULT '',
    duration   INTEGER,
    kind       TEXT NOT NULL DEFAULT '',
    first_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS video_snapshots (
    video_id TEXT NOT NULL,
    taken_at INTEGER NOT NULL,
    views    INTEGER,
    likes    INTEGER,
    comments INTEGER,
    pos      INTEGER,
    PRIMARY KEY (video_id, taken_at)
);
CREATE INDEX IF NOT EXISTS videos_channel ON videos(channel_id);
"""


def db_path(root: Path) -> Path:
    p = Path(root) / "data" / DB_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _connect(root: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(root), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


# ── channels ─────────────────────────────────────────────────────────────────

def add_channel(root: Path, channel_id: str, *, handle: str = "", title: str = "",
                note: str = "", max_videos: int = 0) -> tuple[dict, bool]:
    """(channel, created). Re-adding refreshes handle/title and keeps the history."""
    if not channel_id:
        raise ValueError("channel_id is required")
    with _LOCK, _connect(root) as con:
        existing = con.execute("SELECT 1 FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
        if existing:
            con.execute("UPDATE channels SET handle=COALESCE(NULLIF(?,''),handle), "
                        "title=COALESCE(NULLIF(?,''),title), note=COALESCE(NULLIF(?,''),note), "
                        "max_videos=COALESCE(NULLIF(?,0),max_videos) "
                        "WHERE channel_id=?", (handle, title, note, max_videos, channel_id))
        else:
            con.execute("INSERT INTO channels(channel_id,handle,title,note,added_at,max_videos) "
                        "VALUES(?,?,?,?,?,?)",
                        (channel_id, handle, title, note, int(time.time()), max_videos or 30))
        row = con.execute("SELECT * FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
    return dict(row), not existing


def remove_channel(root: Path, channel_id: str) -> bool:
    """Stop tracking. The history stays, so re-adding picks up where it left off."""
    with _LOCK, _connect(root) as con:
        cur = con.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
    return cur.rowcount > 0


def find_channel(root: Path, ref: str) -> dict | None:
    """By channel id, @handle (with or without @) or exact title, case-insensitive."""
    ref = (ref or "").strip()
    if not ref:
        return None
    handle = "@" + ref.lstrip("@")
    with _connect(root) as con:
        row = con.execute(
            "SELECT * FROM channels WHERE channel_id=? OR lower(handle)=lower(?) OR lower(title)=lower(?)",
            (ref, handle, ref)).fetchone()
    return dict(row) if row else None


def list_channels(root: Path) -> list[dict]:
    """Tracked channels with their latest snapshot (if any)."""
    with _connect(root) as con:
        rows = con.execute(
            "SELECT c.*, s.taken_at AS last_taken, s.subscribers, s.views, s.videos, s.source, "
            "(SELECT COUNT(*) FROM channel_snapshots x WHERE x.channel_id=c.channel_id) AS snapshots "
            "FROM channels c LEFT JOIN channel_snapshots s ON s.channel_id=c.channel_id "
            "AND s.taken_at=(SELECT MAX(taken_at) FROM channel_snapshots WHERE channel_id=c.channel_id) "
            "ORDER BY lower(c.title)").fetchall()
    return [dict(r) for r in rows]


# ── snapshots ────────────────────────────────────────────────────────────────

def record_snapshot(root: Path, channel_id: str, *, subscribers=None, views=None,
                    videos=None, source: str = "", uploads: list[dict] | None = None,
                    taken_at: int | None = None) -> int:
    """Store one reading of a channel and its recent uploads. Returns ``taken_at``."""
    ts = int(taken_at if taken_at is not None else time.time())
    with _LOCK, _connect(root) as con:
        con.execute("INSERT OR REPLACE INTO channel_snapshots VALUES(?,?,?,?,?,?)",
                    (channel_id, ts, subscribers, views, videos, source))
        positions: dict[str, int] = {}
        for v in uploads or []:
            vid = v.get("id")
            if not vid:
                continue
            kind = v.get("kind") or ""
            pos = positions[kind] = positions.get(kind, -1) + 1
            con.execute(
                "INSERT INTO videos(video_id,channel_id,title,published,duration,kind,first_seen) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET "
                "title=excluded.title, published=COALESCE(NULLIF(excluded.published,''),videos.published), "
                "duration=COALESCE(excluded.duration,videos.duration), kind=excluded.kind",
                (vid, channel_id, v.get("title") or "", v.get("published") or "",
                 v.get("duration"), v.get("kind") or "", ts))
            con.execute("INSERT OR REPLACE INTO video_snapshots VALUES(?,?,?,?,?,?)",
                        (vid, ts, v.get("views"), v.get("likes"), v.get("comments"), pos))
    return ts


def _snapshots(con, channel_id: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM channel_snapshots WHERE channel_id=? ORDER BY taken_at", (channel_id,))]


def _delta(a, b):
    return None if a is None or b is None else b - a


def report(root: Path, channel_id: str, since_ts: int, *, top: int = 5) -> dict:
    """What changed for one channel between the first snapshot at/after ``since_ts``
    (or the last one before it, when that is all there is) and the latest.

    Returns {"snapshots", "start", "end", "days", "subscribers", "views",
    "videos", "mixed_sources", "new_uploads", "top_gainers"}.
    """
    with _connect(root) as con:
        snaps = _snapshots(con, channel_id)
        out: dict = {"snapshots": len(snaps), "start": None, "end": None}
        if not snaps:
            return out
        end = snaps[-1]
        before = [s for s in snaps if s["taken_at"] <= since_ts]
        after = [s for s in snaps if s["taken_at"] >= since_ts]
        start = before[-1] if before else after[0]
        out.update(start=start, end=end)
        if start["taken_at"] == end["taken_at"]:
            return out
        days = (end["taken_at"] - start["taken_at"]) / 86400
        out["days"] = days
        for k in ("subscribers", "views", "videos"):
            out[k] = _delta(start[k], end[k])
        window = [s for s in snaps if start["taken_at"] <= s["taken_at"] <= end["taken_at"]]
        out["mixed_sources"] = len({s["source"] for s in window}) > 1

        first_ever = snaps[0]["taken_at"]
        start_day = time.strftime("%Y-%m-%d", time.gmtime(start["taken_at"]))
        new = con.execute(
            "SELECT v.* FROM videos v "
            "LEFT JOIN video_snapshots f ON f.video_id=v.video_id AND f.taken_at=v.first_seen "
            "WHERE v.channel_id=? AND ("
            " (v.published<>'' AND v.published>=?) OR"
            " (v.published='' AND v.first_seen>? AND v.first_seen>=? AND f.pos < ("
            "   SELECT MIN(o.pos) FROM video_snapshots o JOIN videos ov ON ov.video_id=o.video_id"
            "   WHERE o.taken_at=v.first_seen AND ov.channel_id=v.channel_id"
            "   AND ov.kind=v.kind AND ov.first_seen<v.first_seen))"
            ") ORDER BY COALESCE(NULLIF(v.published,''), v.first_seen) DESC",
            (channel_id, start_day, first_ever, start["taken_at"])).fetchall()
        out["new_uploads"] = [dict(r) for r in new]

        gains = con.execute(
            "SELECT v.video_id, v.title, v.published, v.kind, a.views AS views_start, b.views AS views_end, "
            "b.views - a.views AS gained "
            "FROM videos v "
            "JOIN video_snapshots a ON a.video_id=v.video_id AND a.taken_at=("
            "  SELECT MIN(taken_at) FROM video_snapshots WHERE video_id=v.video_id AND taken_at>=?) "
            "JOIN video_snapshots b ON b.video_id=v.video_id AND b.taken_at=("
            "  SELECT MAX(taken_at) FROM video_snapshots WHERE video_id=v.video_id AND taken_at<=?) "
            "WHERE v.channel_id=? AND a.taken_at<b.taken_at AND b.views > a.views "
            "ORDER BY gained DESC LIMIT ?",
            (start["taken_at"], end["taken_at"], channel_id, top)).fetchall()
        out["top_gainers"] = [dict(r) for r in gains]
    return out
