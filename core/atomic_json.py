"""One safe way to persist a JSON document to ``data/``.

Several stores historically did a naive read-modify-``write_text``: no temp file
(a crash mid-write truncates the file) and no lock (concurrent writers lose
updates). ``env_store`` already got this right — atomic tmp-file + fsync +
``os.replace``, with an in-place fallback for Docker's single-file bind mounts.
This module lifts that pattern out so every JSON store can share it instead of
re-deriving it (usually badly).

Scope note: the lock here is a ``threading.Lock`` — it serialises writers *within
one process*. ``os.replace`` guarantees a reader never sees a torn file across
processes, but two processes writing the same file can still lose an update. That
is a deliberate, documented limit for now (see docs/ARCHITECTURE_AUDIT.md §5);
the shared stores that matter are single-writer in practice.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# One lock per absolute path, created on demand. A global lock would needlessly
# serialise writes to unrelated files; a per-path lock keeps them independent.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def read_json(path: Path | str, default):
    """Parse ``path`` as JSON, returning ``default`` on any read/parse failure.

    ``default`` is returned (not raised) for a missing file, invalid JSON, or an
    encoding error, because these stores must degrade to "empty" rather than take
    down the surface that reads them.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return default


def write_json(path: Path | str, data, *, indent: int = 2) -> None:
    """Atomically write ``data`` as JSON to ``path``.

    Serialised per-path within the process; atomic across processes for readers
    (``os.replace``). Falls back to an in-place truncate-write only when the
    atomic rename is impossible (a bind-mounted single file), keeping a ``.bak``
    first so a crash mid-fallback cannot destroy the previous contents.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=indent)
    with _lock_for(p):
        tmp = p.with_name(p.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            return
        except OSError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        # Fallback path (bind-mounted file): keep a backup, then overwrite.
        try:
            if p.exists():
                bak = p.with_name(p.name + ".bak")
                bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
        with open(p, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
