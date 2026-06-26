"""Path-confinement helpers shared by the filesystem and SMB tools.

A raw `str.startswith` check is unsafe for path confinement: an allowed root of
`/Ablage` would also admit a sibling like `/Ablage_secret`. These helpers use a
path-boundary test (exact match or `<root>/...`) and resolve `..`/symlinks first
so traversal cannot escape the configured roots.
"""
from __future__ import annotations

import os


def _resolve(path: str) -> str | None:
    try:
        return os.path.realpath(path)
    except Exception:
        return None


def is_within(path: str, root: str) -> bool:
    """True if `path` is `root` itself or lives under it (boundary-aware)."""
    abs_path = _resolve(path)
    if abs_path is None:
        return False
    real_root = os.path.realpath(root)
    return abs_path == real_root or abs_path.startswith(real_root + os.sep)


def is_within_any(path: str, roots) -> bool:
    """True if `path` is within any of the allowed roots."""
    abs_path = _resolve(path)
    if abs_path is None:
        return False
    for root in roots:
        if not root:
            continue
        real_root = os.path.realpath(root)
        if abs_path == real_root or abs_path.startswith(real_root + os.sep):
            return True
    return False
