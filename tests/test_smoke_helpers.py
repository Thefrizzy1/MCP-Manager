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
