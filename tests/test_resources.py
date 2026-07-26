"""plutus:// resources (workstream B3): the library resource must refuse to read
outside the research library — traversal and symlink escapes included."""
from __future__ import annotations

import os

import pytest
from mcp.server.fastmcp import FastMCP

from tools.resources import register_resource_tools, safe_library_path


def test_safe_library_path_allows_inside(tmp_path):
    base = tmp_path / "lib"
    base.mkdir()
    (base / "note.md").write_text("hi", encoding="utf-8")
    assert safe_library_path(str(base), "note.md") == os.path.realpath(str(base / "note.md"))


def test_safe_library_path_blocks_traversal(tmp_path):
    base = tmp_path / "lib"
    base.mkdir()
    (tmp_path / "secret.txt").write_text("s", encoding="utf-8")
    assert safe_library_path(str(base), "../secret.txt") is None
    assert safe_library_path(str(base), "../../etc/passwd") is None
    assert safe_library_path(str(base), "/etc/passwd") is None
    assert safe_library_path("", "note.md") is None  # no library configured -> refuse


def test_safe_library_path_blocks_symlink_escape(tmp_path):
    base = tmp_path / "lib"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = base / "link.txt"
    try:
        os.symlink(str(outside), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    assert safe_library_path(str(base), "link.txt") is None


def test_resource_families_registered():
    m = FastMCP("t")
    register_resource_tools(m)
    static = {str(r.uri) for r in m._resource_manager.list_resources()}
    templates = {t.uri_template for t in m._resource_manager.list_templates()}
    assert {"plutus://connections", "plutus://health/latest"} <= static
    assert {"plutus://agent-runs/{run_id}", "plutus://library/{path}"} <= templates
