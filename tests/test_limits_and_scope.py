"""Three failures that all looked like something else.

- A run died on a per-minute rate limit at step nine of ten, throwing away
  everything before it.
- Unticking a connection did not actually stop the agent using it, because the
  ACL only gated tools somebody had listed on a service card.
- An agent reported "updated Obsidian Vault/Notes.md" after writing to Plutus's
  own library, because the tool's reply echoed the path without naming the store.
"""
from __future__ import annotations

import pytest

from core import agent_runner as AR
from core import ai_providers as AP
from core import library as LIB


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(AP, "_sleep", lambda s: None)


# ── retrying a limit ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("error,code", [
    ("Quota exceeded", 429),
    ("rate limit reached", 0),
    ("The model is overloaded", 0),
    ("Too Many Requests", 0),
    ("", 503),
    ("", 529),
])
def test_limits_are_recognised(error, code):
    assert AP.is_rate_limited(error, code) is True


@pytest.mark.parametrize("error,code", [
    ("API key not valid", 401),
    ("model not found", 404),
    ("", 200),
])
def test_real_failures_are_not_mistaken_for_limits(error, code):
    """Retrying a bad key just wastes the user's time three times over."""
    assert AP.is_rate_limited(error, code) is False


def test_a_transient_limit_is_retried_not_fatal(tmp_path, monkeypatch):
    """A per-minute limit clears in seconds; losing the run to it does not."""
    acct = AP.add_account(tmp_path, "gemini", "P")
    AP.save_token(tmp_path, "gemini", acct["id"], "k")
    AP.forget_models()
    calls = {"n": 0}

    def fake(method, url, key, *, payload=None, timeout=60, headers=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"code": 429, "json": {}, "error": "Quota exceeded"}
        return {"code": 200, "error": "", "json": {
            "candidates": [{"content": {"parts": [{"text": "recovered"}]}}]}}

    monkeypatch.setattr(AP, "_http", fake)
    res = AP.api_generate(tmp_path, "gemini", acct["id"], "hi", model="m")

    assert res["ok"] is True and res["text"] == "recovered"
    assert calls["n"] == 3, "it should have retried twice before succeeding"


def test_retries_are_bounded(tmp_path, monkeypatch):
    acct = AP.add_account(tmp_path, "gemini", "P")
    AP.save_token(tmp_path, "gemini", acct["id"], "k")
    AP.forget_models()
    calls = {"n": 0}

    def fake(method, url, key, *, payload=None, timeout=60, headers=None):
        calls["n"] += 1
        return {"code": 429, "json": {}, "error": "Quota exceeded"}

    monkeypatch.setattr(AP, "_http", fake)
    res = AP.api_generate(tmp_path, "gemini", acct["id"], "hi", model="m")

    assert res["ok"] is False and "quota" in res["error"]
    assert calls["n"] == AP.RETRY_ATTEMPTS


def test_a_bad_key_is_not_retried(tmp_path, monkeypatch):
    acct = AP.add_account(tmp_path, "gemini", "P")
    AP.save_token(tmp_path, "gemini", acct["id"], "k")
    AP.forget_models()
    calls = {"n": 0}

    def fake(method, url, key, *, payload=None, timeout=60, headers=None):
        calls["n"] += 1
        return {"code": 401, "json": {}, "error": "API key not valid"}

    monkeypatch.setattr(AP, "_http", fake)
    AP.api_generate(tmp_path, "gemini", acct["id"], "hi", model="m")
    assert calls["n"] == 1


# ── falling back to another account ──────────────────────────────────────────

def test_a_second_account_is_found_for_failover(tmp_path):
    a = AP.add_account(tmp_path, "gemini", "One")
    AP.save_token(tmp_path, "gemini", a["id"], "k1")
    b = AP.add_account(tmp_path, "gemini", "Two")
    AP.save_token(tmp_path, "gemini", b["id"], "k2")

    assert AR._spare_account(tmp_path, "gemini", a["id"]) == b["id"]
    assert AR._spare_account(tmp_path, "gemini", b["id"]) == a["id"]


def test_an_unlinked_second_account_is_not_a_fallback(tmp_path):
    a = AP.add_account(tmp_path, "gemini", "One")
    AP.save_token(tmp_path, "gemini", a["id"], "k1")
    AP.add_account(tmp_path, "gemini", "No key")
    assert AR._spare_account(tmp_path, "gemini", a["id"]) == ""


def test_failover_stays_on_the_same_provider(tmp_path):
    """A model id chosen for one provider means nothing to another, and switching
    silently would change both the cost and what the run can do."""
    a = AP.add_account(tmp_path, "gemini", "One")
    AP.save_token(tmp_path, "gemini", a["id"], "k1")
    o = AP.add_account(tmp_path, "openrouter", "Router")
    AP.save_token(tmp_path, "openrouter", o["id"], "k2")

    assert AR._spare_account(tmp_path, "gemini", a["id"]) == ""


def test_a_limited_run_switches_accounts_and_keeps_going(tmp_path, monkeypatch):
    """The run's work so far is worth more than the account it was using."""
    a = AP.add_account(tmp_path, "gemini", "One")
    AP.save_token(tmp_path, "gemini", a["id"], "k1")
    b = AP.add_account(tmp_path, "gemini", "Two")
    AP.save_token(tmp_path, "gemini", b["id"], "k2")
    AP.forget_models()
    monkeypatch.setattr(AR, "load_agent_config",
                        lambda root: {**AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})

    used: list[str] = []

    def fake_turn(root, provider, account_id, **kw):
        used.append(account_id)
        if account_id == a["id"]:
            return {"ok": False, "text": "", "calls": [], "raw_message": {},
                    "error": "rate limit reached", "model": "m", "finish": "", "parts": []}
        return {"ok": True, "text": "finished on the spare", "calls": [],
                "raw_message": {}, "error": "", "model": "m", "finish": "STOP",
                "parts": []}

    monkeypatch.setattr(AP, "api_turn", fake_turn)
    rec = AR.run_agent(tmp_path, "task", label="x", provider="gemini",
                       account_id=a["id"], mcp_url="")

    assert rec["ok"] is True and rec["result"] == "finished on the spare"
    assert used == [a["id"], b["id"]]
    assert rec["account_id"] == b["id"], "the record names the account that answered"
    assert any("switching to" in ln for ln in rec["log"]), rec["log"]


# ── the connection picker actually gates ─────────────────────────────────────

def test_every_service_tool_is_gated_not_just_the_carded_ones():
    """A card lists the tools worth a button, not every tool an integration
    registers — and the ACL iterates this map, so anything missing was never
    gated. Unticking Nextcloud still left upload, move and delete available."""
    from core.dashboard_api import tool_to_service_map
    from ui.runtime import tools

    tmap = tool_to_service_map()
    ungated = [n for n in tools.tool_names() if n not in tmap]

    # Delegation belongs to no service by design; everything else must be owned.
    assert set(ungated) <= {"agent_delegate", "agent_delegate_batch",
                            "agent_list_workers"}, ungated
    for name in ("nextcloud_upload_file", "nextcloud_delete_file",
                 "nextcloud_move_file", "fs_delete"):
        assert tmap.get(name), f"{name} is not gated by any connection"


def test_prefixes_are_learned_from_the_cards_not_hardcoded():
    from core.service_registry import service_tool_map

    services = [{"id": "nextcloud", "tools": [{"name": "nextcloud_get_events"}]}]
    out = service_tool_map(".", services,
                           tool_names=["nextcloud_get_events", "nextcloud_upload_file"])
    assert out["nextcloud_upload_file"] == "nextcloud"


def test_a_prefix_two_services_share_is_left_alone():
    """Guessing an owner would gate a tool behind the wrong connection."""
    from core.service_registry import service_tool_map

    services = [{"id": "a", "tools": [{"name": "shared_one"}]},
                {"id": "b", "tools": [{"name": "shared_two"}]}]
    out = service_tool_map(".", services, tool_names=["shared_one", "shared_three"])
    assert "shared_three" not in out


def test_unticking_a_connection_now_denies_its_write_tools():
    from ui.runtime import _agent_service_disallow

    denied = _agent_service_disallow(["jellyfin"])       # everything else off
    assert "mcp__plutus__nextcloud_upload_file" in denied
    assert "mcp__plutus__nextcloud_delete_file" in denied


# ── the library says which store it is ───────────────────────────────────────

def test_writing_names_the_store_so_it_cannot_be_passed_off_as_nextcloud(tmp_path):
    """The reported failure: an agent asked to update a Nextcloud file passed
    "Obsidian Vault/Titlesideas.md" to the library tool, got back "Wrote Obsidian
    Vault/Titlesideas.md", and reported success on a file Nextcloud never saw."""
    msg = LIB.write_note("Obsidian Vault/Titlesideas.md", "# ideas\n", root=tmp_path)

    assert "research library" in msg
    assert "NOT Nextcloud" in msg
    assert "nextcloud_" in msg, "it should point at the tool that would have worked"
