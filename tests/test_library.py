"""The research library — the app's own writable directory for agent output.

The failure this exists to stop: an agent asked to write a file answered, quite
truthfully, that it had no way to. Every writable path came from
``FILESYSTEM_ALLOWED_PATHS``, which is a list of the operator's NAS shares on a
real install and empty on a fresh one, and the configured library default was the
host path ``/data/library`` that exists on nobody's machine. So the product's own
"research library" was both invisible in the Files page and refused by its own
filesystem tools.
"""
from __future__ import annotations

import os
import zipfile

import pytest

from core import library as LIB


def test_the_library_lives_inside_the_apps_persisted_data(tmp_path):
    """Not a host mount: ./data is already the volume that survives an update."""
    d = LIB.library_dir(tmp_path)
    assert d == tmp_path / "data" / "library"
    assert not d.exists(), "library_dir must not have side effects"


def test_ensure_library_creates_it_with_a_readme(tmp_path):
    d = LIB.ensure_library(tmp_path)
    assert d.is_dir()
    text = (d / "README.md").read_text(encoding="utf-8")
    assert "Files page" in text and "fs_write_file" in text


def test_ensure_library_does_not_clobber_an_existing_readme(tmp_path):
    d = LIB.ensure_library(tmp_path)
    (d / "README.md").write_text("my own notes", encoding="utf-8")
    LIB.ensure_library(tmp_path)
    assert (d / "README.md").read_text(encoding="utf-8") == "my own notes"


def test_the_library_is_confined_like_every_other_root(tmp_path):
    LIB.ensure_library(tmp_path)
    assert LIB.is_in_library(str(tmp_path / "data" / "library" / "notes" / "a.md"), tmp_path)
    assert not LIB.is_in_library(str(tmp_path / "data" / "secrets.json"), tmp_path)
    assert not LIB.is_in_library(str(tmp_path / "data" / "library_other" / "x"), tmp_path)
    # Traversal out of the root must not be admitted either.
    assert not LIB.is_in_library(
        str(tmp_path / "data" / "library" / ".." / ".." / "etc"), tmp_path)


# ── the filesystem tools can actually reach it ───────────────────────────────

def test_the_filesystem_tools_allow_the_library_without_editing_the_allowlist():
    """The whole point: writing a note must not require the operator to add the
    app's own directory to FILESYSTEM_ALLOWED_PATHS first."""
    from config import cfg
    from core.path_guard import is_within_any

    roots = list(cfg.filesystem_allowed_paths) + LIB.library_roots()
    target = os.path.join(str(LIB.library_dir()), "research", "findings.md")
    assert is_within_any(target, roots)

    # And it is genuinely additive — the host allowlist is unchanged.
    assert not is_within_any(target, list(cfg.filesystem_allowed_paths))


def test_the_agent_library_default_points_at_it(tmp_path):
    from core import agent_runner as AR

    lib, hint = AR.resolve_library({**AR.DEFAULT_AGENT_CONFIG, "output_mode": "filesystem"})
    assert lib == str(LIB.library_dir())
    assert "always writable" in hint and "Files page" in hint


def test_an_explicit_library_path_still_wins(tmp_path):
    from core import agent_runner as AR

    lib, _ = AR.resolve_library({"output_mode": "filesystem",
                                 "fs_library_path": "/mnt/share/notes/"})
    assert lib == "/mnt/share/notes"


# ── getting the work back out ────────────────────────────────────────────────

@pytest.fixture
def files_client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ui.api import files as F
    from ui.api.deps import verify_auth

    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setattr(F, "_fs_roots", list)          # no host mounts in the test
    app = FastAPI()
    app.include_router(F.router)
    app.dependency_overrides[verify_auth] = lambda: None
    return TestClient(app), LIB.ensure_library(tmp_path)


def test_the_library_is_a_browsable_root_that_exists(files_client):
    client, lib = files_client
    roots = client.get("/api/v1/files/list?path=").json()["items"]
    entry = next(r for r in roots if r["kind"] == "internal")
    assert entry["name"] == "Research library"
    assert entry["exists"] is True, "the root used to point at a path nobody had"
    assert entry["path"] == str(lib)


def test_a_researched_structure_comes_back_as_one_zip(files_client):
    """An agent builds a folder of notes and a dashboard; downloading that a file
    at a time is not a way to get it out."""
    client, lib = files_client
    (lib / "topic").mkdir()
    (lib / "topic" / "notes.md").write_text("# Findings", encoding="utf-8")
    (lib / "topic" / "sub").mkdir()
    (lib / "topic" / "sub" / "dashboard.html").write_text("<h1>hi</h1>", encoding="utf-8")

    r = client.get(f"/api/v1/files/download-folder?path={lib / 'topic'}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="topic.zip"' in r.headers["content-disposition"]

    import io
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = sorted(n.replace("\\", "/") for n in z.namelist())
        assert names == ["notes.md", "sub/dashboard.html"]
        assert z.read("notes.md").decode() == "# Findings"


def test_a_folder_outside_the_roots_is_refused(files_client, tmp_path):
    client, _ = files_client
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert client.get(f"/api/v1/files/download-folder?path={outside}").status_code == 403


def test_zipping_a_file_is_a_clear_error_not_a_crash(files_client):
    client, lib = files_client
    (lib / "a.md").write_text("x", encoding="utf-8")
    assert client.get(f"/api/v1/files/download-folder?path={lib / 'a.md'}").status_code == 400
