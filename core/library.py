"""The research library — the app's own writable directory for agent output.

An agent that researches something has to be able to *keep* it: build a folder
structure, write Markdown notes, drop an HTML dashboard, and have all of it show
up in the Files page ready to download. Before this, the only writable places
were the host paths in ``FILESYSTEM_ALLOWED_PATHS`` — which on a fresh install is
somebody's NAS shares or nothing at all — so an agent asked to write a file got

    Error: Path '/data/library' is not in allowed directories: /Ablage, /Backup

and reported, correctly but uselessly, that it had no way to create files.

So the library lives inside the app instead: ``<root>/data/library``, on the
volume that is already persisted (``./data:/app/data``), created on demand, and
allowed for the filesystem tools *without* the operator editing an allowlist. It
is app storage, not a host mount — the allowlist exists to gate access to the
host, and this is not the host.

That is the whole security argument, and it is why this is a separate root rather
than a quiet entry appended to ``filesystem_allowed_paths``: confinement to this
one directory is still enforced by the same boundary-aware check, and nothing
here widens reach to anything outside it.
"""
from __future__ import annotations

import os
from pathlib import Path

LIBRARY_DIRNAME = "library"

# Written into new libraries so the first thing an agent (or a person) opening
# the folder sees is what it is for.
_README = """# Research library

Anything Plutus's agents research and write lands here, and everything here is
visible in the Files page and downloadable from it.

Agents reach it with the filesystem tools — `fs_write_file`, `fs_read_file`,
`fs_list_directory`, `fs_search_files`. Folders are fine: ask for a structure and
you get one.

This directory is inside the app's persisted `data/` volume, so it survives
updates and container recreation.
"""


def default_root() -> Path:
    """The project root, resolved from this file.

    Self-contained on purpose: the filesystem *tools* need the library path, and
    reaching into ui.runtime for ROOT from tools/ would close an import cycle
    (ui.runtime imports the tool registrars).
    """
    return Path(__file__).resolve().parent.parent


def library_dir(root: Path | None = None) -> Path:
    """``<root>/data/library``. Not created — see ensure_library."""
    return Path(root or default_root()) / "data" / LIBRARY_DIRNAME


def ensure_library(root: Path | None = None) -> Path:
    """The library directory, created if missing, with a README on first use."""
    d = library_dir(root)
    try:
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.md"
        if not readme.exists():
            readme.write_text(_README, encoding="utf-8")
    except OSError:
        pass
    return d


def library_roots(root: Path | None = None) -> list[str]:
    """Roots the filesystem tools may always reach, on top of the host allowlist."""
    return [str(library_dir(root))]


def is_in_library(path: str, root: Path | None = None) -> bool:
    from core.path_guard import is_within

    return is_within(path, str(library_dir(root)))


# ── operations, addressed relative to the library root ───────────────────────
#
# Agents get *relative* paths ("research/findings.md"), never absolute ones. It
# is easier for a model to get right, and it makes confinement a property of the
# API rather than something each caller has to remember to check.

MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_LISTED = 500


class LibraryError(ValueError):
    """A path that would leave the library, or an operation that cannot be done."""


def resolve_in_library(rel: str, root: Path | None = None) -> Path:
    """An absolute path inside the library, or LibraryError."""
    base = Path(os.path.realpath(str(ensure_library(root))))
    cleaned = str(rel or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned or cleaned in (".", ".."):
        raise LibraryError("give a path inside the library, e.g. 'research/notes.md'")
    target = Path(os.path.realpath(str(base / cleaned)))
    # realpath first, then the boundary test: a symlink or ".." inside the string
    # must not be able to point out of the library.
    if target != base and not str(target).startswith(str(base) + os.sep):
        raise LibraryError(f"'{rel}' is outside the research library")
    return target


def write_note(rel: str, content: str, *, append: bool = False,
               root: Path | None = None) -> str:
    if len(content.encode("utf-8", "ignore")) > MAX_NOTE_BYTES:
        raise LibraryError(f"content is larger than {MAX_NOTE_BYTES // 1024} KB")
    target = resolve_in_library(rel, root)
    target.parent.mkdir(parents=True, exist_ok=True)      # structures, not just files
    with open(target, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    verb = "Appended to" if append else "Wrote"
    return f"{verb} {relative_name(str(target), root)} ({len(content)} chars)"


def read_note(rel: str, root: Path | None = None) -> str:
    target = resolve_in_library(rel, root)
    if not target.is_file():
        raise LibraryError(f"'{rel}' does not exist in the research library")
    return target.read_text(encoding="utf-8", errors="replace")


def list_dir(rel: str = "", root: Path | None = None) -> str:
    base = ensure_library(root)
    target = base if not (rel or "").strip() else resolve_in_library(rel, root)
    if not target.is_dir():
        raise LibraryError(f"'{rel}' is not a folder in the research library")
    rows: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if len(rows) >= MAX_LISTED:
            rows.append(f"… more than {MAX_LISTED} entries, narrow the path")
            break
        if entry.is_dir():
            rows.append(f"{entry.name}/")
        else:
            try:
                rows.append(f"{entry.name} ({entry.stat().st_size} B)")
            except OSError:
                rows.append(entry.name)
    where = relative_name(str(target), root)
    return f"{where}:\n" + ("\n".join(rows) if rows else "(empty)")


def relative_name(path: str, root: Path | None = None) -> str:
    """A library-relative label for the UI, falling back to the basename."""
    try:
        return str(Path(os.path.realpath(path)).relative_to(
            Path(os.path.realpath(str(library_dir(root))))))
    except (ValueError, OSError):
        return os.path.basename(str(path).rstrip("/\\")) or "library"
