"""/app must serve the *built* React shell, not a hand-written one.

This is the regression that made every frontend change invisible: render_spa()
returned its own shell loading the legacy pre-React ui/static/spa.js, while the
Vite build sat unserved in ui/static/dist. The image contained the new UI, it was
fetchable at /spa/assets/…, and nobody ever saw it.
"""
from __future__ import annotations

import ui.spa_page as SP


def test_serves_the_built_vite_shell_when_present(tmp_path, monkeypatch):
    index = tmp_path / "index.html"
    index.write_text(
        '<!doctype html><html><head>'
        '<script type="module" crossorigin src="/spa/assets/index-abc123.js"></script>'
        '</head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(SP, "_DIST_INDEX", index)

    out = SP.render_spa()
    assert "/spa/assets/index-abc123.js" in out
    # The legacy bundle must never be referenced again.
    assert "/static/spa.js" not in out


def test_missing_build_fails_loudly_instead_of_serving_the_legacy_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(SP, "_DIST_INDEX", tmp_path / "does-not-exist.html")

    out = SP.render_spa()
    assert "not been built" in out
    assert "/static/spa.js" not in out, "a silent fallback is what hid the bug for months"
    assert "npm --prefix ui/web run build" in out


def test_dist_available_reflects_the_file(tmp_path, monkeypatch):
    missing = tmp_path / "nope.html"
    monkeypatch.setattr(SP, "_DIST_INDEX", missing)
    assert SP.dist_available() is False

    missing.write_text("<html></html>", encoding="utf-8")
    assert SP.dist_available() is True


def test_the_real_repo_build_is_wired_to_the_spa_mount():
    """Against the actual checkout: if a build exists it must point at /spa/."""
    if not SP.dist_available():
        import pytest
        pytest.skip("ui/static/dist not built in this checkout")
    out = SP.render_spa()
    assert "/spa/assets/" in out
    assert "/static/spa.js" not in out


# ── one broken card must not take the app with it ────────────────────────────
#
# There is no JS test runner in this repo, so this is a structural guard rather
# than a render test. It is worth having anyway: the failure it protects against
# is a blank white page whose only trace is a console warning, and it came back
# the moment a component read a field a service had not returned.

def _src(rel: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ui" / "web" / "src" / rel).read_text(encoding="utf-8")


def test_pages_render_inside_an_error_boundary():
    app = _src("App.tsx")
    assert "ErrorBoundary" in app, "a page throw would unmount the whole root"
    # Keyed by route, or the boundary latches and every page after the broken
    # one renders the error instead of itself.
    assert "key={route}" in app


def test_the_shell_itself_has_a_backstop():
    """The per-page boundary lives inside App and cannot catch a throw above it."""
    assert "ErrorBoundary" in _src("main.tsx")


def test_the_boundary_reports_the_real_error():
    """On a self-hosted box the person seeing this is the person who can fix it,
    so "something went wrong" is worth less than the actual message."""
    boundary = _src("components/ErrorBoundary.tsx")
    assert "error.message" in boundary
    assert "getDerivedStateFromError" in boundary
    # Recoverable without a page reload.
    assert "Try again" in boundary
