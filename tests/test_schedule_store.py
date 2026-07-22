"""Schedule store — CRUD + cron validation (offline)."""
import pytest

from core import schedule_store as ss


def test_validate_cron():
    assert ss.validate_cron("0 3 * * *")[0]
    assert ss.validate_cron("*/15 * * * *")[0]
    assert not ss.validate_cron("0 3 * *")[0]        # 4 fields
    assert not ss.validate_cron("0 3 * * * *")[0]    # 6 fields
    assert not ss.validate_cron("bad 3 * * *")[0]    # letters


def test_add_and_get_agent_schedule(tmp_path):
    entry = ss.add_schedule(tmp_path, {
        "name": "nightly scan", "kind": "agent", "cron": "0 3 * * *",
        "payload": {"prompt": "check my homelab and report"},
    })
    assert entry["id"]
    assert ss.get_schedule(tmp_path, entry["id"])["name"] == "nightly scan"


def test_add_tool_schedule(tmp_path):
    entry = ss.add_schedule(tmp_path, {
        "kind": "tool", "cron": "*/30 * * * *",
        "payload": {"tool": "sonarr_queue", "params": {}},
    })
    assert entry["kind"] == "tool"


def test_agent_requires_prompt(tmp_path):
    with pytest.raises(ValueError):
        ss.add_schedule(tmp_path, {"kind": "agent", "cron": "0 3 * * *", "payload": {}})


def test_tool_requires_tool_name(tmp_path):
    with pytest.raises(ValueError):
        ss.add_schedule(tmp_path, {"kind": "tool", "cron": "0 3 * * *", "payload": {"params": {}}})


def test_bad_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        ss.add_schedule(tmp_path, {"kind": "nope", "cron": "0 3 * * *", "payload": {}})


def test_bad_cron_rejected(tmp_path):
    with pytest.raises(ValueError):
        ss.add_schedule(tmp_path, {"kind": "agent", "cron": "nope", "payload": {"prompt": "x"}})


def test_update_and_delete(tmp_path):
    e = ss.add_schedule(tmp_path, {"kind": "agent", "cron": "0 3 * * *", "payload": {"prompt": "x"}})
    upd = ss.update_schedule(tmp_path, e["id"], {"kind": "agent", "cron": "0 4 * * *",
                                                 "enabled": False, "payload": {"prompt": "y"}})
    assert upd["cron"] == "0 4 * * *"
    assert upd["enabled"] is False
    assert upd["id"] == e["id"]  # id preserved
    assert ss.delete_schedule(tmp_path, e["id"]) is True
    assert ss.get_schedule(tmp_path, e["id"]) is None


def test_delete_missing_returns_false(tmp_path):
    assert ss.delete_schedule(tmp_path, "nope") is False
