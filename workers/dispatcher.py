#!/usr/bin/env python3
"""workers/dispatcher.py — runs the studio's agents on their tasks.

Runs on a machine with a Claude login. In a loop it asks Plutus for the next
task one of its agents can do, runs that agent as an unattended session with
only the Plutus tools the agent needs, and reports the outcome. One task at a
time. A used-up usage window hands the task back and waits for the reset.

    python3 dispatcher.py          run forever
    python3 dispatcher.py once     do at most one task, then exit

An agent = a brief (workers/agents/<agent>.md) + a tool list. No shell and no
file access on this machine: documents go through jobs_read_doc/jobs_write_doc.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("lab", HERE.parent / "lab" / "lab.py")
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)

COMMON = "jobs_get jobs_board jobs_read_doc jobs_write_doc library_list library_read studio_tasks studio_thread".split()
WEB = "web_search web_fetch web_read exa_search".split()
TOOLS = {
    "manager": COMMON + "studio_delegate jobs_update calendar_add_event calendar_events lab_list".split(),
    "researcher": COMMON + WEB + ("youtube_search youtube_video youtube_watch youtube_transcript youtube_comments "
                                  "reddit_search reddit_post_comments huggingface_model_info huggingface_search_models "
                                  "github_repo_info trend_radar_candidate trend_radar_list").split(),
    "writer": COMMON + "youtube_transcript youtube_video web_read".split(),
    "editor": COMMON + "studio_delegate studio_view_image".split(),
    "motion": COMMON + ["studio_view_image"],
    "thumbnail": COMMON + "studio_view_image youtube_search youtube_video trend_radar_title_research".split(),
    "shorts": COMMON + "studio_view_image youtube_transcript youtube_video youtube_comments".split(),
    "publisher": COMMON + "youtube_video youtube_keywords youtube_search trend_radar_title_research".split(),
}
TIMEOUT_MIN = {"manager": 12, "researcher": 35, "writer": 30, "editor": 25, "motion": 20,
               "thumbnail": 20, "shorts": 25, "publisher": 20}
MODEL_FLAG = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku", "fable": ""}
# What each kind of task asks for, appended to the agent's brief.
ASK = {
    "intake": "A new video idea was just added. Set the production up as your brief describes.",
    "chat": "The owner wrote to you in this video's chat. Answer the last message as your brief describes.",
    "research": "Research this video as your brief describes.",
    "script": "Write (or revise) this video's script as your brief describes.",
    "thumbnail": "Develop thumbnail concepts for this video as your brief describes.",
    "edit_plan": "Plan the cut of this video as your brief describes.",
    "motion": "List and specify the graphics this video needs as your brief describes.",
    "shorts": "Find the short-form moments in this video as your brief describes.",
    "upload_pack": "Prepare the upload pack for this video as your brief describes.",
}


def build_prompt(task: dict) -> str:
    brief = (HERE / "agents" / f"{task['agent']}.md").read_text()
    shared = (HERE / "agents" / "_shared.md").read_text()
    parts = [brief, shared, "## This task", ASK[task["kind"]],
             f"- Video (job id): `{task['job']}`" if task["job"] != "channel" else "- About: the whole channel, no single video",
             f"- Task id: {task['id']} · today is {dt.date.today().isoformat()}"]
    if task.get("instruction"):
        parts.append("- What was asked:\n\n" + task["instruction"])
    if task.get("thread"):
        parts.append("## The conversation so far (oldest first)\n" + "\n".join(
            f"{m.get('agent') or ('Owner' if m['role'] == 'user' else m['role'])}: {m['text']}" for m in task["thread"]))
    return "\n\n".join(parts)


def run_one(cfg: dict, machine: str) -> str:
    """Take and run one task. Returns 'none', 'done', 'failed' or 'limit'."""
    raw = lab.plutus(cfg, "studio_next_task", {"params": {"agents_json": json.dumps(list(TOOLS)), "machine": machine}},
                     timeout=30)
    task = json.loads(raw or "{}")
    if not task:
        return "none"
    agent = task["agent"]
    lab.log(f"task {task['id']}: {task['kind']} for {agent} on {task['job']}")
    run_dir = Path(cfg["state_dir"]) / "studio" / f"{task['id']:06d}-{task['kind']}"
    run_dir.mkdir(parents=True, exist_ok=True)

    def finish(status: str, result: str = "", reason: str = "") -> None:
        try:
            lab.plutus(cfg, "studio_finish_task", {"params": {"task_id": task["id"], "status": status,
                                                              "result": result[:19000], "reason": reason[:900]}}, timeout=30)
        except Exception as e:
            lab.log(f"could not report task {task['id']}: {e}")

    try:
        settings = {w["id"]: w for w in json.loads(lab.plutus(cfg, "worker_settings", timeout=20))}
        cfg = dict(cfg, claude_model=MODEL_FLAG.get((settings.get(agent) or {}).get("model", ""), ""))
        out = run_dir / "session.log"
        lab.run_session(cfg, build_prompt(task), run_dir, out,
                        allowed=[f"mcp__plutus__{t}" for t in TOOLS[agent]],
                        timeout_min=TIMEOUT_MIN[agent], mcp=lab.write_mcp_config(cfg, run_dir))
        lab.check_usage(out)
    except lab.UsageLimit as e:
        finish("queued", reason=f"Claude usage limit reached; continues {e.resume_at:%H:%M}" if e.resume_at else "Claude usage limit reached")
        wait = max(120, (e.resume_at - dt.datetime.now()).total_seconds() + 120) if e.resume_at else 1800
        lab.log(f"usage limit; sleeping {wait / 60:.0f} min")
        time.sleep(min(wait, 6 * 3600))
        return "limit"
    except Exception as e:
        finish("failed", reason=f"the session could not run: {e}")
        return "failed"
    res = lab.session_outcome(out)
    text = str(res.get("result") or "").strip()
    if res.get("is_error") or not text:
        finish("failed", reason=(text or "the session ended without an answer")[:800])
        return "failed"
    m = lab.re.search(r"^NOT REQUIRED:\s*(.+)$", text, lab.re.M)
    if m:
        finish("not_required", reason=m.group(1).strip())
    else:
        finish("done", result=text[-6000:])
    return "done"


def main() -> int:
    cfg = lab.load_config()
    machine = cfg.get("machine_id", "")
    once = len(sys.argv) > 1 and sys.argv[1] == "once"
    lab.log(f"dispatcher on {machine or 'this machine'} for: {', '.join(TOOLS)}")
    while True:
        try:
            state = run_one(cfg, machine)
        except Exception as e:
            state = "error"
            lab.log(f"round failed: {e}")
        if once:
            print(state)
            return 0
        if state in ("none", "error"):
            time.sleep(15)


if __name__ == "__main__":
    sys.exit(main())
