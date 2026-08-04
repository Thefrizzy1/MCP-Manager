"""File-manager surface: browse/read/download/delete inside the allowed roots."""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from core import library
from config import cfg
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])


def _fs_roots() -> list[str]:
    return list(cfg.filesystem_allowed_paths or [])


def _internal_roots() -> list[str]:
    """Internal app storage the agents write to (the research library).

    Always the app's own ``data/library``, created on demand — not the configured
    ``fs_library_path``, which defaulted to the host path ``/data/library`` that
    exists on nobody's install. The Files page therefore showed a "Research
    library" root that was permanently missing, while agents were refused write
    access to it by the filesystem allowlist. See core/library.py.
    """
    return [str(library.ensure_library(ROOT))]


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


# Agents are asked to build *structures* — a folder of notes, an HTML dashboard
# and its assets. Downloading that a file at a time is not a way to get it out,
# so a directory comes down as one zip.
_MAX_ZIP_FILES = 5000
_MAX_ZIP_BYTES = 512 * 1024 * 1024


@router.get("/api/v1/files/download-folder")
async def api_v1_files_download_folder(request: Request):
    """Zip a directory and stream it. Symlinks are skipped, not followed."""
    from core.path_guard import is_within_any
    roots = _all_roots()
    path = (request.query_params.get("path") or "").strip()
    if not is_within_any(path, roots):
        raise HTTPException(403, "Path is outside the allowed directories")
    ap = os.path.realpath(path)
    if not os.path.isdir(ap):
        raise HTTPException(400, "Not a directory")

    buf = io.BytesIO()
    total = count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, names in os.walk(ap, followlinks=False):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(folder, d))]
            for name in names:
                fp = os.path.join(folder, name)
                # A symlink inside an allowed root can still point outside it.
                if os.path.islink(fp) or not is_within_any(fp, roots):
                    continue
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                count += 1
                total += size
                if count > _MAX_ZIP_FILES or total > _MAX_ZIP_BYTES:
                    raise HTTPException(
                        413, "That folder is too large to zip — download a subfolder.")
                try:
                    z.write(fp, os.path.relpath(fp, ap))
                except OSError:
                    continue
    buf.seek(0)
    name = _header_safe(os.path.basename(ap.rstrip("/\\"))) or "library"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


def _header_safe(name: str) -> str:
    """A folder name that can sit inside a quoted header parameter.

    On Linux — which is where this runs — a directory may legitimately be called
    `report"; x="`, and interpolating that straight into Content-Disposition ends
    the quoted string and injects another parameter. Non-ASCII goes too: header
    values are latin-1 on the wire, so a folder with an em dash in its name would
    raise on encode and turn a download into a 500.
    """
    cleaned = "".join(c for c in name if c.isascii() and c.isprintable()
                      and c not in '"\\')
    return cleaned.strip() or ""


class FilePathBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)


class DeleteBody(FilePathBody):
    # Deleting a folder takes everything in it, so it is never implied — the UI
    # asks, and the API refuses a non-empty directory without this.
    recursive: bool = False


def _guard_target(path: str) -> str:
    """An allowed, existing path that is not one of the roots themselves."""
    from core.path_guard import is_within_any
    roots = _all_roots()
    if not is_within_any(path, roots):
        raise HTTPException(403, "Path is outside the allowed directories")
    ap = os.path.realpath(path)
    if any(ap == os.path.realpath(r) for r in roots):
        raise HTTPException(400, "That is a root folder — it cannot be deleted from here")
    return ap


@router.post("/api/v1/files/delete")
async def api_v1_files_delete(body: DeleteBody):
    """Delete a file, or a folder and its contents.

    Folders used to be refused outright ("Only files can be deleted here"), which
    left the file manager unable to clean up anything an agent had built — a
    research run that made a folder tree could only be dismantled a file at a time,
    and the now-empty folders stayed forever.
    """
    import shutil

    ap = _guard_target(body.path)
    if os.path.islink(ap):
        try:
            os.unlink(ap)                      # remove the link, never its target
        except OSError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "deleted": "link"}
    if os.path.isfile(ap):
        try:
            os.remove(ap)
        except OSError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "deleted": "file"}
    if not os.path.isdir(ap):
        raise HTTPException(404, "Nothing there")

    if not body.recursive and any(os.scandir(ap)):
        raise HTTPException(409, "That folder is not empty — confirm to delete its contents")
    try:
        shutil.rmtree(ap) if body.recursive else os.rmdir(ap)
    except OSError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "deleted": "folder"}


class MkdirBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)   # parent directory
    name: str = Field(..., min_length=1, max_length=255)


def _safe_child(parent: str, name: str) -> str:
    """A new entry directly inside `parent`, with no traversal in the name."""
    from core.path_guard import is_within_any
    clean = os.path.basename(name.strip().replace("\\", "/").rstrip("/"))
    if not clean or clean in (".", ".."):
        raise HTTPException(400, "Invalid name")
    if not is_within_any(parent, _all_roots()) or not os.path.isdir(os.path.realpath(parent)):
        raise HTTPException(403, "Target folder is outside the allowed directories")
    target = os.path.realpath(os.path.join(os.path.realpath(parent), clean))
    if not is_within_any(target, _all_roots()):
        raise HTTPException(403, "Path is outside the allowed directories")
    return target


@router.post("/api/v1/files/mkdir")
async def api_v1_files_mkdir(body: MkdirBody):
    # Rejected rather than normalised. A browser may legitimately send a path as
    # an upload's filename, so uploads take the basename — but a person typing
    # "../escape" into a New folder box has made a mistake, and quietly creating
    # "escape" somewhere else is a worse answer than saying so.
    raw = body.name.strip()
    if raw in (".", "..") or "/" in raw or "\\" in raw:
        raise HTTPException(400, "A folder name cannot contain '/' or '\\'")
    target = _safe_child(body.path, raw)
    if os.path.exists(target):
        raise HTTPException(409, "Already exists")
    try:
        os.makedirs(target)
    except OSError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "path": target}


# Uploads are capped because the target can be a mounted share and the body is
# buffered per chunk into a real file; without a limit a stray multi-GB upload
# fills whatever volume it lands on.
MAX_UPLOAD_BYTES = 256 * 1024 * 1024


@router.post("/api/v1/files/upload")
async def api_v1_files_upload(request: Request):
    """Upload one or more files into a folder inside the allowed roots."""
    form = await request.form()
    parent = str(form.get("path") or "").strip()
    uploads = [f for f in form.getlist("file") if hasattr(f, "filename")]
    if not parent:
        raise HTTPException(400, "No target folder")
    if not uploads:
        raise HTTPException(400, "No file uploaded")

    written = []
    for up in uploads:
        target = _safe_child(parent, up.filename or "upload")
        size = 0
        try:
            with open(target, "wb") as fh:
                while chunk := await up.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        fh.close()
                        os.remove(target)
                        raise HTTPException(
                            413, f"'{up.filename}' is larger than "
                                 f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                    fh.write(chunk)
        except OSError as e:
            raise HTTPException(400, str(e))
        written.append({"name": os.path.basename(target), "path": target, "size": size})
    return {"ok": True, "files": written}
