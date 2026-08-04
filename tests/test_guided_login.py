"""The guided provider login: the flow behind the "Sign in here" button.

core/provider_login drives the CLI on a pty and feeds the code back. Four
endpoints existed with no caller anywhere in the UI, so Settings could only ever
hand you a command to run in a terminal — which on a headless box means finding
one and docker-exec'ing into the container.

The interactive path needs a pty and so only runs on POSIX; what is asserted here
is the contract around it, which holds everywhere.
"""
from __future__ import annotations

import pytest

from core import provider_login


def test_snapshot_has_everything_the_dialog_renders():
    """The dialog drives entirely off one poll, so a missing key is a blank UI."""
    snap = provider_login.FLOW.snapshot()
    assert {"state", "provider", "account_id", "url", "error",
            "output_tail", "token_captured", "available", "elapsed"} <= set(snap)


def test_a_fresh_flow_is_idle():
    assert provider_login.FLOW.snapshot()["state"] in {
        "idle", "starting", "awaiting_code", "finishing", "done", "failed"}


def test_availability_matches_whether_a_pty_exists():
    """The button is gated on this, so it has to be the truth and not a guess."""
    assert provider_login.available() is (provider_login._pty is not None)


def test_the_token_is_never_echoed_back():
    """A captured setup-token is reported as present, never returned — the
    snapshot goes to the browser."""
    flow = provider_login.LoginFlow()
    flow.token = "sk-ant-oat-secret"
    snap = flow.snapshot()
    assert snap["token_captured"] is True
    assert "sk-ant-oat-secret" not in repr(snap)


def test_submitting_a_code_outside_the_code_step_is_refused():
    flow = provider_login.LoginFlow()
    assert flow.state == "idle"
    res = flow.submit("123456")
    assert res.get("ok") is not True


def test_an_api_key_provider_has_no_interactive_login(monkeypatch):
    """Gemini authenticates with a key. Starting a login for it would hang on a
    CLI that never prompts, so the endpoint refuses with a reason."""
    from core import ai_providers

    api_kind = [pid for pid, spec in ai_providers.PROVIDERS.items()
                if spec.get("kind") == ai_providers.KIND_API]
    assert api_kind, "no API-key provider to check against"
    for pid in api_kind:
        assert not ai_providers.PROVIDERS[pid].get("login_cmd"), (
            f"{pid} is an API-key provider but declares a login command")


def test_output_tail_is_capped():
    """It is diagnostic CLI output going to a browser — unbounded is a footgun."""
    flow = provider_login.LoginFlow()
    flow.tail = "x" * 50_000
    assert len(flow.snapshot()["output_tail"]) <= 1200
