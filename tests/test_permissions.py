"""Permissions: defaults, the approval queue, and that only a decision grants."""
import pytest

from core import permissions as P


def test_defaults_and_unknown_actions(tmp_path):
    assert P.check(tmp_path, "lab.merge_pr") == "ask"
    assert P.check(tmp_path, "lab.download_models") == "allow"
    assert P.check(tmp_path, "rm.everything") == "never"


def test_ask_waits_for_a_decision_and_does_not_duplicate(tmp_path):
    r = P.request(tmp_path, "lab.merge_pr", "Merge LTX workflow", "https://x/pr/1", ref="pr-1")
    assert r["status"] == "pending"
    assert P.request(tmp_path, "lab.merge_pr", "again", ref="pr-1")["id"] == r["id"]
    with pytest.raises(ValueError):
        P.mark_done(tmp_path, r["id"])            # not approved yet
    assert P.decide(tmp_path, r["id"], True)["status"] == "approved"
    assert P.mark_done(tmp_path, r["id"])["status"] == "done"
    with pytest.raises(ValueError):
        P.decide(tmp_path, r["id"], True)         # a decision is final


def test_allow_is_approved_at_once_and_never_refuses(tmp_path):
    P.set_value(tmp_path, "lab.merge_pr", "allow")
    assert P.request(tmp_path, "lab.merge_pr", "Merge it", ref="pr-2")["status"] == "approved"
    P.set_value(tmp_path, "lab.merge_pr", "never")
    with pytest.raises(PermissionError):
        P.request(tmp_path, "lab.merge_pr", "Merge it", ref="pr-3")
    with pytest.raises(ValueError):
        P.set_value(tmp_path, "lab.merge_pr", "sometimes")


def test_the_mcp_surface_cannot_grant():
    from ui.runtime import tools
    names = set(tools.tool_names())
    assert {"studio_permissions", "studio_request_approval", "studio_approval_done"} <= names
    assert not any("decide" in n or "set_permission" in n for n in names)
