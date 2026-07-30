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


# ── a file manager that can actually manage files ────────────────────────────

def test_a_folder_can_be_deleted_with_its_contents(files_client):
    """Folders used to be refused outright, so a research run that built a tree
    could only be dismantled one file at a time — and the empty folders stayed."""
    client, lib = files_client
    (lib / "topic" / "sub").mkdir(parents=True)
    (lib / "topic" / "sub" / "a.md").write_text("x", encoding="utf-8")

    # Not implied: a non-empty folder needs the caller to say so.
    r = client.post("/api/v1/files/delete", json={"path": str(lib / "topic")})
    assert r.status_code == 409 and "not empty" in r.json()["detail"]
    assert (lib / "topic").is_dir()

    r = client.post("/api/v1/files/delete",
                    json={"path": str(lib / "topic"), "recursive": True})
    assert r.status_code == 200 and r.json()["deleted"] == "folder"
    assert not (lib / "topic").exists()


def test_an_empty_folder_needs_no_ceremony(files_client):
    client, lib = files_client
    (lib / "empty").mkdir()
    assert client.post("/api/v1/files/delete", json={"path": str(lib / "empty")}).status_code == 200


def test_a_root_folder_cannot_be_deleted(files_client):
    """"Clean up the library" must never be able to mean "remove the library"."""
    client, lib = files_client
    r = client.post("/api/v1/files/delete", json={"path": str(lib), "recursive": True})
    assert r.status_code == 400 and "root folder" in r.json()["detail"]
    assert lib.is_dir()


def test_deleting_outside_the_roots_is_refused(files_client, tmp_path):
    client, _ = files_client
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    r = client.post("/api/v1/files/delete", json={"path": str(outside), "recursive": True})
    assert r.status_code == 403 and outside.is_dir()


def test_a_folder_can_be_created(files_client):
    client, lib = files_client
    r = client.post("/api/v1/files/mkdir", json={"path": str(lib), "name": "notes"})
    assert r.status_code == 200 and (lib / "notes").is_dir()
    assert client.post("/api/v1/files/mkdir",
                       json={"path": str(lib), "name": "notes"}).status_code == 409


@pytest.mark.parametrize("name", ["../escape", "..", "a/b", "", "   "])
def test_a_new_folder_name_cannot_traverse(files_client, name):
    client, lib = files_client
    r = client.post("/api/v1/files/mkdir", json={"path": str(lib), "name": name})
    assert r.status_code in (400, 403, 409, 422), r.text
    assert not (lib.parent / "escape").exists()


def test_files_can_be_uploaded_into_a_folder(files_client):
    client, lib = files_client
    r = client.post("/api/v1/files/upload",
                    data={"path": str(lib)},
                    files=[("file", ("notes.md", b"# hello", "text/markdown")),
                           ("file", ("data.csv", b"a,b\n1,2", "text/csv"))])
    assert r.status_code == 200
    assert {f["name"] for f in r.json()["files"]} == {"notes.md", "data.csv"}
    assert (lib / "notes.md").read_bytes() == b"# hello"


def test_an_upload_cannot_be_written_outside_the_roots(files_client, tmp_path):
    client, lib = files_client
    r = client.post("/api/v1/files/upload",
                    data={"path": str(lib)},
                    files=[("file", ("../../escaped.txt", b"x", "text/plain"))])
    # The name is reduced to its basename, so it lands inside — never above.
    assert r.status_code == 200
    assert (lib / "escaped.txt").is_file()
    assert not (tmp_path / "escaped.txt").exists()


def test_uploading_into_somewhere_not_allowed_is_refused(files_client, tmp_path):
    client, _ = files_client
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    r = client.post("/api/v1/files/upload", data={"path": str(outside)},
                    files=[("file", ("x.txt", b"x", "text/plain"))])
    assert r.status_code == 403 and not (outside / "x.txt").exists()


# ── the built-in tools an agent always has ───────────────────────────────────

def test_writing_builds_the_folder_structure_as_it_goes(tmp_path):
    """"Research this and write it up" means folders, not one flat file."""
    msg = LIB.write_note("research/topic/findings.md", "# Findings\n", root=tmp_path)
    assert "findings.md" in msg
    assert (tmp_path / "data" / "library" / "research" / "topic" / "findings.md").is_file()
    assert LIB.read_note("research/topic/findings.md", root=tmp_path) == "# Findings\n"


def test_appending_adds_rather_than_replaces(tmp_path):
    LIB.write_note("a.md", "one\n", root=tmp_path)
    LIB.write_note("a.md", "two\n", append=True, root=tmp_path)
    assert LIB.read_note("a.md", root=tmp_path) == "one\ntwo\n"


def test_listing_shows_folders_and_sizes(tmp_path):
    LIB.write_note("notes/a.md", "hello", root=tmp_path)
    out = LIB.list_dir("", root=tmp_path)
    assert "notes/" in out and "README.md" in out
    assert "a.md" in LIB.list_dir("notes", root=tmp_path)


@pytest.mark.parametrize("bad", [
    "../../.env", "..", "", "   ", "research/../../../secrets.json",
    "notes/../../../../etc/passwd",
])
def test_a_path_that_would_leave_the_library_is_refused(tmp_path, bad):
    with pytest.raises(LIB.LibraryError):
        LIB.resolve_in_library(bad, tmp_path)


@pytest.mark.parametrize("given", ["/etc/passwd", "/research/notes.md", "\\notes\\a.md"])
def test_an_absolute_looking_path_is_pulled_into_the_library(tmp_path, given):
    """Models write '/research/notes.md' as often as 'research/notes.md'.
    Rejecting that would be pedantry; escaping on it would be a hole. It is
    treated as library-relative, and what matters is that it lands inside."""
    target = LIB.resolve_in_library(given, tmp_path)
    assert LIB.is_in_library(str(target), tmp_path)


def test_reading_something_that_is_not_there_says_so(tmp_path):
    with pytest.raises(LIB.LibraryError, match="does not exist"):
        LIB.read_note("nope.md", root=tmp_path)


def test_an_oversized_note_is_refused_before_it_is_written(tmp_path):
    with pytest.raises(LIB.LibraryError, match="larger than"):
        LIB.write_note("big.md", "x" * (LIB.MAX_NOTE_BYTES + 1), root=tmp_path)


# ── they are tools, and they never raise at the agent ────────────────────────

def test_the_builtin_tools_are_declared_and_callable(tmp_path):
    from core import agent_tools as AT

    names = [t["name"] for t in AT.LIBRARY_TOOLS]
    assert names == ["library_write_file", "library_read_file", "library_list_files"]
    assert all(AT.is_library_tool(n) for n in names)
    assert not AT.is_library_tool("nextcloud_upload_file")

    out = AT.call_library_tool("library_write_file",
                               {"path": "x/y.md", "content": "hi"}, root=tmp_path)
    assert out["is_error"] is False
    assert AT.call_library_tool("library_read_file", {"path": "x/y.md"},
                                root=tmp_path)["text"] == "hi"


def test_a_bad_call_comes_back_as_data_not_an_exception(tmp_path):
    """A tool error is something the model reads and works around; raising here
    would end the whole run."""
    from core import agent_tools as AT

    out = AT.call_library_tool("library_read_file", {"path": "../../.env"}, root=tmp_path)
    assert out["is_error"] is True and "outside the research library" in out["text"]

    assert AT.call_library_tool("library_nope", {}, root=tmp_path)["is_error"] is True


def test_the_builtin_tools_convert_into_geminis_dialect():
    """They ride in the same declarations array as the MCP tools, so they face
    the same validation — one bad schema fails every call in the run."""
    from core import agent_tools as AT
    from tests.test_agent_tools import keywords_outside_the_subset

    decls, dropped = AT.gemini_declarations(AT.LIBRARY_TOOLS, None)
    assert dropped == 0 and len(decls) == 3
    for d in decls:
        assert keywords_outside_the_subset(d["parameters"]) == []
    write = next(d for d in decls if d["name"] == "library_write_file")
    assert write["parameters"]["required"] == ["path", "content"]


def test_an_operator_can_still_deny_them():
    from core import agent_tools as AT

    kept = AT.library_tools_for(["mcp__plutus__library_write_file"])
    assert [t["name"] for t in kept] == ["library_read_file", "library_list_files"]
