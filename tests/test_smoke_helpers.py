"""Parsers used by the behavioral smoke round-trips."""
from core.smoke_service_tools import _id_from_text, _uid_from_text


def test_id_from_habitica_add_output():
    out = "✓ Added todo: 'TEST_SMOKE_TODO_123' (ID: a1b2-c3d4-e5)"
    assert _id_from_text(out) == "a1b2-c3d4-e5"


def test_id_absent_returns_empty():
    assert _id_from_text("Error: Habitica not configured.") == ""
    assert _id_from_text("no id here") == ""


def test_uid_from_nextcloud_output():
    assert _uid_from_text("Created. UID: `abc-123@host`") == "abc-123@host"
    assert _uid_from_text("UID: plain-uid") == "plain-uid"


def test_uid_absent_returns_empty():
    assert _uid_from_text("no uid present") == ""


# ── the filesystem smoke path ────────────────────────────────────────────────

def test_the_fs_smoke_path_is_an_allowed_root_not_slash(monkeypatch):
    """`/` is refused on every install — the whole point of
    filesystem_allowed_paths is that `/` is not one of them. Three fs tools
    therefore failed everywhere, with a message that read like a permissions bug."""
    from config import cfg
    from core.tool_registry import merged_smoke_payload

    monkeypatch.setattr(cfg, "filesystem_allowed_paths", ["/srv/share", "/mnt/backup"],
                        raising=False)
    for tool in ("fs_list_directory", "fs_search_files", "fs_recent_files"):
        assert merged_smoke_payload(tool)["path"] == "/srv/share", tool


def test_an_absent_root_is_skipped_for_one_that_exists(monkeypatch, tmp_path):
    """A configured-but-unmounted root is worth reporting, but not on every tool
    at once when another root is right there and would prove the tool works."""
    from config import cfg
    from core.tool_registry import merged_smoke_payload

    monkeypatch.setattr(cfg, "filesystem_allowed_paths",
                        ["/definitely/not/mounted", str(tmp_path)], raising=False)
    assert merged_smoke_payload("fs_list_directory")["path"] == str(tmp_path)


def test_with_no_roots_configured_it_stays_put(monkeypatch):
    from config import cfg
    from core.tool_registry import merged_smoke_payload

    monkeypatch.setattr(cfg, "filesystem_allowed_paths", [], raising=False)
    assert merged_smoke_payload("fs_list_directory")["path"] == "/"


def test_a_platform_without_the_dependency_is_unconfigured_not_broken():
    """Docker is reached over a Unix socket, which Windows has no AF_UNIX for.
    Every Docker tool used to die with "unexpected error (AttributeError)" — a
    message that says nothing and counts as a failure."""
    from core.tool_registry import looks_like_missing_service_config

    msg = ("Error: Docker is not available on this platform — it is reached over a "
           "Unix socket, which this OS does not provide.")
    assert looks_like_missing_service_config(msg)
