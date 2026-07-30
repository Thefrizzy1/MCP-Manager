"""Run transcripts: what the agent actually did.

The console log only ever held one-line summaries — assistant text clipped to 160
chars, tool arguments to 110 — so after a run there was no way to see which tools
ran, with what arguments, or what came back. A run could report success while you
could not find the note it claimed to have written.
"""
from __future__ import annotations

import json

from core import agent_runner as AR


def _assistant(blocks):
    return {"type": "assistant", "message": {"content": blocks}}


# ── event -> transcript rows ─────────────────────────────────────────────────

def test_tool_calls_keep_their_full_arguments():
    ev = _assistant([{"type": "tool_use", "id": "t1",
                      "name": "mcp__plutus__nextcloud_create_note",
                      "input": {"title": "Digest", "content": "x" * 300,
                                "folder": "research/2026"}}])
    rows = AR.transcript_entries(ev)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "tool_call"
    assert r["name"] == "mcp__plutus__nextcloud_create_note"
    # The arguments are what tell you *where* the note went — they must survive.
    assert "research/2026" in r["input"]
    assert "Digest" in r["input"]


def test_assistant_text_is_not_clipped_to_a_console_line():
    long = "Findings: " + ("y" * 1000)
    rows = AR.transcript_entries(_assistant([{"type": "text", "text": long}]))
    assert rows[0]["kind"] == "assistant"
    assert len(rows[0]["text"]) > 500, "transcript must keep more than the console's 160 chars"


def test_thinking_blocks_are_captured():
    rows = AR.transcript_entries(_assistant([{"type": "thinking", "thinking": "weigh options"}]))
    assert rows[0]["kind"] == "thinking" and "weigh options" in rows[0]["text"]


def test_tool_results_are_captured_including_errors():
    ev = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
         "content": [{"type": "text", "text": "Error: Path not in allowed directories."}]}]}}
    rows = AR.transcript_entries(ev)
    assert rows[0]["kind"] == "tool_result"
    assert rows[0]["is_error"] is True
    assert "not in allowed directories" in rows[0]["text"]


def test_tool_result_accepts_a_plain_string_body():
    ev = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t2", "content": "done"}]}}
    assert AR.transcript_entries(ev)[0]["text"] == "done"


def test_session_row_records_what_the_agent_could_reach():
    """Answers 'did it even have the Nextcloud tools?' without guessing."""
    ev = {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6",
          "cwd": "/app", "tools": ["Read", "mcp__plutus__nextcloud_create_note"],
          "mcp_servers": [{"name": "plutus", "status": "connected"}]}
    r = AR.transcript_entries(ev)[0]
    assert r["kind"] == "session"
    assert "mcp__plutus__nextcloud_create_note" in r["tools"]
    assert r["model"] == "claude-sonnet-4-6"


def test_final_row_carries_cost_and_turns():
    ev = {"type": "result", "subtype": "success", "total_cost_usd": 1.04,
          "num_turns": 12, "result": "Wrote the digest."}
    r = AR.transcript_entries(ev)[0]
    assert r["kind"] == "final" and r["cost_usd"] == 1.04 and r["turns"] == 12


def test_oversized_content_is_clipped_with_a_marker_not_dropped():
    rows = AR.transcript_entries(_assistant([{"type": "text", "text": "z" * 20000}]))
    text = rows[0]["text"]
    assert len(text) < 20000
    assert "more chars" in text, "clipping must be visible, not silent"


def test_unknown_events_produce_nothing():
    assert AR.transcript_entries({"type": "something_new"}) == []
    assert AR.transcript_entries({}) == []


# ── persistence ──────────────────────────────────────────────────────────────

class _Proc:
    returncode = 0
    stderr = None

    def __init__(self, lines):
        self.stdout = lines

    def wait(self): return 0
    def kill(self): pass


def test_a_run_persists_its_transcript(tmp_path, monkeypatch, agent_preconditions):
    lines = [
        json.dumps({"type": "system", "subtype": "init", "model": "sonnet",
                    "cwd": "/app", "tools": ["mcp__plutus__nextcloud_create_note"]}) + "\n",
        json.dumps(_assistant([{"type": "tool_use", "id": "t1",
                                "name": "mcp__plutus__nextcloud_create_note",
                                "input": {"title": "Digest", "folder": "research"}}])) + "\n",
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "created research/Digest.md"}]}}) + "\n",
        json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.2,
                    "num_turns": 3, "result": "done"}) + "\n",
    ]
    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})
    monkeypatch.setattr(AR.subprocess, "Popen", lambda *a, **k: _Proc(lines))

    rec = AR.run_agent(tmp_path, "write a digest", label="digest")

    saved = AR.get_transcript(tmp_path, rec["id"])
    assert saved is not None
    kinds = [e["kind"] for e in saved]
    assert kinds == ["session", "tool_call", "tool_result", "final"]
    # The thing the user actually wanted: where did the note go?
    assert "research" in saved[1]["input"]
    assert "created research/Digest.md" in saved[2]["text"]
    assert rec["transcript_entries"] == 4


def test_transcript_lookup_is_guarded_and_absent_is_none(tmp_path):
    assert AR.get_transcript(tmp_path, "nope") is None
    assert AR.get_transcript(tmp_path, "../../etc/passwd") is None
    assert AR.get_transcript(tmp_path, "") is None


def test_transcript_sidecar_is_not_mistaken_for_a_run(tmp_path, monkeypatch, agent_preconditions):
    """Transcripts must stay out of data/agent_runs/. Written there as
    `<id>.transcript.json` they matched list_runs()' `*.json` glob, appearing as a
    phantom run and making _index_rebuild raise on a JSON list."""
    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})
    monkeypatch.setattr(AR.subprocess, "Popen", lambda *a, **k: _Proc([
        json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.5,
                    "num_turns": 1, "result": "ok"}) + "\n"]))

    AR.run_agent(tmp_path, "hi", label="one")
    runs = AR.list_runs(tmp_path, 50)
    assert len(runs) == 1, runs
    assert AR.total_cost(tmp_path) == 0.5
