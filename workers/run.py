#!/usr/bin/env python3
"""workers/run.py — runs one worker's shift as an unattended Claude session.

    python3 run.py researcher        the Researcher's nightly round
    python3 run.py manager           the YouTube Manager's round (dates from mail)

A worker is a prompt (workers/prompts/<worker>.md) plus the short list of
Plutus tools it may use. It gets no shell and no file access on this machine:
everything it does goes through those tools. Which model it runs is read from
Plutus (Overview → click the worker). A used-up Claude usage window is
reported as "paused", not as a failed shift.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("lab", HERE.parent / "lab" / "lab.py")
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)

READ_WEB = "web_search web_fetch web_read exa_search".split()
TOOLS = {
    "researcher": (
        "worker_report jobs_log jobs_board db_write_note db_search_notes db_read_note lab_queue lab_list "
        "calendar_add_event calendar_events trend_radar_collect trend_radar_list trend_radar_candidate "
        "trend_radar_signals trend_radar_done youtube_search youtube_channel youtube_channel_videos "
        "youtube_video youtube_watch youtube_transcript youtube_comments youtube_track youtube_track_report "
        "youtube_analytics reddit_search reddit_subreddit_posts reddit_post_comments "
        "huggingface_search_models huggingface_trending_models huggingface_model_info "
        "github_search_repos github_repo_info").split() + READ_WEB,
    # Mail is read-only for the manager: it can look, never send, move or delete.
    "manager": (
        "worker_report jobs_log jobs_board jobs_get jobs_update calendar_add_event calendar_events "
        "calendar_remove_event db_write_note db_search_notes proton_bridge_status proton_list_folders "
        "proton_list_messages proton_read_message proton_search proton_list_threads proton_read_thread").split(),
}
TIMEOUT_MIN = {"researcher": 45, "manager": 20}
# CLI names for the models a worker can be set to; "" lets the CLI use its default.
MODEL_FLAG = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku", "fable": ""}


def shift(worker: str) -> int:
    if worker not in TOOLS:
        print(f"unknown worker {worker!r}; one of {', '.join(TOOLS)}")
        return 2
    cfg = lab.load_config()
    state = Path(cfg["state_dir"]) / "workers" / worker
    run_dir = state / dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        settings = {w["id"]: w for w in json.loads(lab.plutus(cfg, "worker_settings", timeout=20))}
    except Exception as e:
        lab.log(f"could not read worker settings: {e}")
        settings = {}
    cfg["claude_model"] = MODEL_FLAG.get((settings.get(worker) or {}).get("model", ""), "")
    prompt = (HERE / "prompts" / f"{worker}.md").read_text().replace("{{DATE}}", dt.date.today().isoformat()) \
        .replace("{{NOTES}}", str(state / "NOTES.md"))
    notes = state / "NOTES.md"
    if notes.is_file():
        prompt += "\n\n## Your notes from earlier shifts\n" + notes.read_text()[-6000:]

    def say(text: str, busy: bool) -> None:
        try:
            lab.plutus(cfg, "worker_report", {"params": {"worker": worker, "text": text[:290], "busy": busy,
                                                         "machine": cfg.get("machine_id", "")}}, timeout=20)
        except Exception as e:
            lab.log(f"could not report: {e}")

    say("Shift started", True)
    out = run_dir / "session.log"
    lab.run_session(cfg, prompt, run_dir, out, allowed=[f"mcp__plutus__{t}" for t in TOOLS[worker]],
                    timeout_min=TIMEOUT_MIN[worker], mcp=lab.write_mcp_config(cfg, run_dir))
    try:
        lab.check_usage(out)
    except lab.UsageLimit as e:
        when = f"{e.resume_at:%H:%M}" if e.resume_at else "later"
        say(f"Paused: Claude usage limit reached (resets {when}).", False)
        return 1
    res = lab.session_outcome(out)
    summary = str(res.get("result") or "").strip()
    # The session's last paragraph is its hand-over; keep it for the next shift.
    if summary:
        with notes.open("a") as fh:
            fh.write(f"\n### {dt.date.today().isoformat()}\n{summary[-1500:]}\n")
    if res.get("is_error") or not summary:
        say("Shift ended without a result; see the run log on the server.", False)
        return 1
    say(summary.splitlines()[-1][:280] if summary else "Round finished", False)   # closes the round on its timeline
    return 0


if __name__ == "__main__":
    sys.exit(shift(sys.argv[1] if len(sys.argv) > 1 else ""))
