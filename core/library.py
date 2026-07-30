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


def relative_name(path: str, root: Path | None = None) -> str:
    """A library-relative label for the UI, falling back to the basename."""
    try:
        return str(Path(os.path.realpath(path)).relative_to(
            Path(os.path.realpath(str(library_dir(root))))))
    except (ValueError, OSError):
        return os.path.basename(str(path).rstrip("/\\")) or "library"
