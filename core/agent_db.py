"""Agent DB — a writable destination that cannot be taken away.

Every other place an agent can save something depends on configuration that can be
wrong: Nextcloud credentials, a CalDAV permission, an Obsidian vault path, or a
filesystem allow-list. When any of those is off, an agent researches for minutes
and then has nowhere to put the result — the work is simply lost.

This is the floor beneath all of them: a SQLite file inside Plutus's own data
directory. No credentials, no network, no allow-list. Its tools are in
ALWAYS_EXPOSED so the tool slicer can never remove them, which is exactly the
failure that left an agent reporting "I don't have tools to write files".

Every write reads back what it wrote and returns the stored row. A tool that
reports success without confirming it is how a model ends up telling you it saved
a file that does not exist.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

DB_FILE = "agent_db.sqlite3"
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_created ON notes(created_at DESC);
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


def _row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "title": r["title"], "body": r["body"],
            "tags": [t for t in (r["tags"] or "").split(",") if t],
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def write_note(root: Path, title: str, body: str = "", tags: list[str] | None = None,
               note_id: int | None = None) -> dict:
    """Insert or update, then read the row back and return it.

    The read-back is the point: the caller gets the stored record, so "saved" is a
    fact rather than an assumption.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("a note needs a title")
    tag_s = ",".join(t.strip() for t in (tags or []) if t.strip())
    now = int(time.time())
    with _LOCK, _connect(root) as con:
        if note_id:
            cur = con.execute(
                "UPDATE notes SET title=?, body=?, tags=?, updated_at=? WHERE id=?",
                (title, body or "", tag_s, now, note_id))
            if cur.rowcount == 0:
                raise KeyError(f"no note with id {note_id}")
            rid = note_id
        else:
            cur = con.execute(
                "INSERT INTO notes (title, body, tags, created_at, updated_at) "
                "VALUES (?,?,?,?,?)", (title, body or "", tag_s, now, now))
            rid = cur.lastrowid
        stored = con.execute("SELECT * FROM notes WHERE id=?", (rid,)).fetchone()
    if stored is None:                       # pragma: no cover - would mean a failed commit
        raise RuntimeError("the note did not survive the write")
    return _row(stored)


def read_note(root: Path, note_id: int) -> dict | None:
    with _connect(root) as con:
        r = con.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    return _row(r) if r else None


def list_notes(root: Path, limit: int = 20) -> list[dict]:
    with _connect(root) as con:
        rows = con.execute(
            "SELECT * FROM notes ORDER BY created_at DESC, id DESC LIMIT ?", (max(1, limit),)).fetchall()
    return [_row(r) for r in rows]


def search_notes(root: Path, query: str, limit: int = 20) -> list[dict]:
    q = f"%{(query or '').strip()}%"
    with _connect(root) as con:
        rows = con.execute(
            "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?", (q, q, q, max(1, limit))).fetchall()
    return [_row(r) for r in rows]


def delete_note(root: Path, note_id: int) -> bool:
    with _LOCK, _connect(root) as con:
        cur = con.execute("DELETE FROM notes WHERE id=?", (note_id,))
        gone = con.execute("SELECT 1 FROM notes WHERE id=?", (note_id,)).fetchone() is None
    return cur.rowcount > 0 and gone


def stats(root: Path) -> dict:
    with _connect(root) as con:
        n = con.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]
        last = con.execute("SELECT MAX(updated_at) AS m FROM notes").fetchone()["m"]
    p = db_path(root)
    return {"notes": n, "last_write": last,
            "path": str(p), "size_bytes": p.stat().st_size if p.exists() else 0}
