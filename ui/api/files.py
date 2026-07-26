"""File-manager surface: browse/read/download/delete inside the allowed roots."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import agent_runner
from config import cfg
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])


def _fs_roots() -> list[str]:
    return list(cfg.filesystem_allowed_paths or [])


def _internal_roots() -> list[str]:
    """Internal app storage the agents write to (the research library).
    Kept browsable from the Files page so generated notes are one click away."""
    try:
        acfg = agent_runner.load_agent_config(ROOT)
        lib = str(acfg.get("fs_library_path") or "/data/library").rstrip("/")
    except Exception:
        lib = "/data/library"
    return [lib] if lib else []


def _all_roots() -> list[str]:
    """Every path the file browser may touch: internal storage + mounted shares."""
    return _internal_roots() + _fs_roots()


@router.get("/api/v1/files/list")
async def api_v1_files_list(request: Request):
    """List a directory inside the allowed roots. Empty path = the root groups
    (internal storage first, then mounted shares / allowed paths)."""
    from core.path_guard import is_within_any
    path = (request.query_params.get("path") or "").strip()
    if not path:
        items = []
        for r in _internal_roots():
            items.append({"name": "Research library", "path": r, "type": "dir",
                          "kind": "internal", "exists": os.path.isdir(r)})
        for r in _fs_roots():
            items.append({"name": os.path.basename(r.rstrip("/")) or r,
                          "path": r, "type": "dir", "kind": "mount",
                          "exists": os.path.isdir(r)})
        return {"path": "", "parent": None, "items": items, "is_root": True}
    if not is_within_any(path, _all_roots()):
        raise HTTPException(403, "Path is outside the allowed directories")
    ap = os.path.realpath(path)
    if not os.path.isdir(ap):
        raise HTTPException(400, "Not a directory")
    items = []
    try:
        for name in sorted(os.listdir(ap), key=str.lower):
            fp = os.path.join(ap, name)
            try:
                st = os.stat(fp)
                is_dir = os.path.isdir(fp)
                items.append({"name": name, "path": fp, "type": "dir" if is_dir else "file",
                              "size": 0 if is_dir else st.st_size, "mtime": int(st.st_mtime)})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    parent = os.path.dirname(ap)
    return {"path": ap, "parent": parent if is_within_any(parent, _all_roots()) else "", "items": items}


@router.get("/api/v1/files/read")
async def api_v1_files_read(request: Request):
    """Return a text preview of a file (secrets redacted, size-capped)."""
    from core.path_guard import is_within_any
    from core.redact import redact_secrets
    path = (request.query_params.get("path") or "").strip()
    if not is_within_any(path, _all_roots()):
        raise HTTPException(403, "Path is outside the allowed directories")
    ap = os.path.realpath(path)
    if not os.path.isfile(ap):
        raise HTTPException(404, "Not a file")
    if os.path.getsize(ap) > 1024 * 1024:
        return {"text": "(file larger than 1 MB — download to view)", "truncated": True}
    try:
        txt = Path(ap).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(400, f"Cannot read: {e}")
    red, _ = redact_secrets(txt)
    return {"text": red[:200000], "truncated": len(red) > 200000}


@router.get("/api/v1/files/download")
async def api_v1_files_download(request: Request):
    from core.path_guard import is_within_any
    path = (request.query_params.get("path") or "").strip()
    if not is_within_any(path, _all_roots()):
        raise HTTPException(403, "Path is outside the allowed directories")
    ap = os.path.realpath(path)
    if not os.path.isfile(ap):
        raise HTTPException(404, "Not a file")
    return FileResponse(ap, filename=os.path.basename(ap))


class FilePathBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)


@router.post("/api/v1/files/delete")
async def api_v1_files_delete(body: FilePathBody):
    from core.path_guard import is_within_any
    if not is_within_any(body.path, _all_roots()):
        raise HTTPException(403, "Path is outside the allowed directories")
    ap = os.path.realpath(body.path)
    if not os.path.isfile(ap):
        raise HTTPException(400, "Only files can be deleted here")
    try:
        os.remove(ap)
    except OSError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
