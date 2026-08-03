"""Build the nightly ComfyUI / AI research room and schedule it.

Run it on the box, inside the container:

    docker exec plutus-mcp python scripts/setup_research_room.py

Idempotent: run it again to re-apply the brief and the schedule without
duplicating either. Pass --replace to rebuild the seats from scratch.

Seats are spread across *different* provider accounts on purpose. A room is one
agent run per seat, so putting five seats on one account burns that account's
limit five times in a night; round-robining them means each account contributes
roughly one run. If only one account exists the room still works — it just leans
on that one, and the script says so.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import ai_providers, schedule_store, workforce  # noqa: E402

ROOM_LABEL = "ComfyUI & AI Research"
CRON = "0 3 * * *"          # 03:00 nightly, after the daily backup window
TZ = "Europe/Berlin"

# What every seat can reach. This is also the tool slice — the manifest an agent
# is charged for on every request — so it is the research surface and nothing
# else. No Docker, no SSH: this team reads and writes files, it does not operate
# the homelab.
CONNECTIONS = [
    "websearch", "wikipedia", "firecrawl",      # open web
    "hackernews", "reddit", "stackexchange",    # where practitioners actually talk
    "github", "huggingface", "youtube",         # releases, models, tutorials
    "comfyui",                                  # the local instance, to check what is installed
    "agent_db", "filesystem",                   # somewhere to put the work
]

BRIEF = """\
Nightly research on ComfyUI and the wider local-AI image/video ecosystem.

Work into a folder named for TODAY'S DATE (YYYY-MM-DD) inside your working
folder — call get_context if you are unsure what today is. Everything below goes
in that dated folder, so each night stands on its own and nothing is overwritten.

What the night has to produce, by the end:

  <date>/README.md        what changed since yesterday, worth-reading-first
  <date>/findings/        one markdown file per topic, with source links
  <date>/scripts/         runnable things — workflow JSON, python, shell
  <date>/dashboard.html   a single self-contained page indexing all of it

Standing rules for every seat:

- Cite the source URL for anything you claim is new. "I read that X shipped" with
  no link is not a finding.
- Prefer primary sources: the release, the repo, the model card, the PR. A blog
  post about a release is not the release.
- If something is unchanged since the last run, say so briefly and move on. A
  short honest night is better than a padded one.
- Put real work in files. Only a short summary of your reply reaches the next
  seat, so anything substantial has to be written down to survive.
- If you discover the brief is pointing the room at a dead end, call room_advise
  so the seats after you get the correction as an instruction, not buried in prose.
"""

SEATS = [
    ("ComfyUI watch", "researcher",
     "What changed in ComfyUI itself: core releases, notable custom nodes, "
     "breaking changes, new samplers/schedulers. Check the ComfyUI repo and the "
     "custom-node registries. Also check the local ComfyUI instance to see what "
     "is actually installed, so recommendations are relevant to this setup."),

    ("Model watch", "researcher",
     "New and newly-popular image/video models, checkpoints, LoRAs and VAEs — "
     "Hugging Face trending, model cards, licences, VRAM requirements. Flag "
     "anything that would run on a single consumer GPU, and say when it would not."),

    ("Technique scout", "researcher",
     "Methods rather than releases: sampling, upscaling, control, video "
     "consistency, prompt technique. Papers, threads, and workflows people are "
     "actually getting results from. Note what is hype and what has reproductions."),

    ("Community pulse", "researcher",
     "What practitioners are hitting: Reddit, Hacker News, GitHub issues and "
     "discussions. Recurring bugs, install pain, workarounds, and what people "
     "are asking for. This is the seat that catches problems before they bite."),

    ("Build engineer", "developer",
     "Turn the night's findings into runnable things in scripts/: ComfyUI "
     "workflow JSON, python helpers, install or update commands. Every script "
     "gets a header comment saying what it does, what it needs, and which "
     "finding it came from. Do not invent APIs — if you did not see it "
     "documented, say so in the header instead of guessing."),

    ("Editor", "writer",
     "Write <date>/README.md and <date>/dashboard.html. The dashboard is one "
     "self-contained HTML file, no external CSS or JS — a clear title, the date, "
     "and a titled card per finding linking to its markdown file and any script "
     "it produced. Group by theme, put the most important thing first, and make "
     "it readable on a phone. Read the actual files in the folder rather than "
     "working from what earlier seats said in their replies."),
]


def pick_accounts() -> list[tuple[str, str, str]]:
    """(provider, account_id, label) for every usable account, in a stable order."""
    out: list[tuple[str, str, str]] = []
    for pid, accounts in (ai_providers.load_accounts(ROOT) or {}).items():
        for a in accounts:
            if a.get("id"):
                out.append((pid, a["id"], a.get("label") or a["id"]))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replace", action="store_true",
                    help="rebuild the seats from scratch instead of leaving them alone")
    ap.add_argument("--cron", default=CRON, help=f"cron expression (default {CRON!r})")
    ap.add_argument("--no-schedule", action="store_true", help="build the room but do not schedule it")
    args = ap.parse_args()

    accounts = pick_accounts()
    if not accounts:
        print("No provider accounts are configured. Add at least one under "
              "Settings > AI Providers first — a seat cannot run without one.")
        return 1

    print(f"Provider accounts found: {len(accounts)}")
    for pid, aid, label in accounts:
        print(f"  - {pid}/{aid}  ({label})")
    if len(accounts) == 1:
        print("  ! Only one account, so every seat runs on it. Six runs a night "
              "against one limit — add another account to spread the load.")

    rooms = workforce.load_rooms(ROOT)
    room = next((r for r in rooms if r.get("label") == ROOM_LABEL), None)
    if room and args.replace:
        workforce.delete_room(ROOT, room["id"])
        room = None
        print(f"\nRemoved the existing '{ROOM_LABEL}' room (--replace).")

    if room:
        print(f"\nRoom already exists: {room['id']}")
    else:
        room = workforce.add_room(ROOT, ROOM_LABEL, mcp_services=list(CONNECTIONS))
        print(f"\nCreated room {room['id']}")

    workforce.update_room(ROOT, room["id"],
                          {"brief": BRIEF, "mcp_services": list(CONNECTIONS)})

    if not (room.get("seats") or []):
        for i, (label, role, goal) in enumerate(SEATS):
            provider, account_id, acct_label = accounts[i % len(accounts)]
            workforce.add_seat(ROOT, room["id"], role=role, provider=provider,
                               account_id=account_id, goal=goal, label=label)
            print(f"  seat {i + 1}. {label:16} {role:11} -> {provider}/{acct_label}")
    else:
        print(f"  {len(room['seats'])} seats already staffed — left alone "
              f"(use --replace to rebuild).")

    if args.no_schedule:
        print("\nSkipped scheduling (--no-schedule).")
    else:
        existing = [s for s in schedule_store.load_schedules(ROOT)
                    if s.get("kind") == "room"
                    and (s.get("payload") or {}).get("room_id") == room["id"]]
        entry = {"name": f"Nightly — {ROOM_LABEL}", "kind": "room", "cron": args.cron,
                 "timezone": TZ, "enabled": True, "payload": {"room_id": room["id"]}}
        if existing:
            schedule_store.update_schedule(ROOT, existing[0]["id"], entry)
            print(f"\nUpdated the existing schedule ({existing[0]['id']}): {args.cron} {TZ}")
        else:
            sc = schedule_store.add_schedule(ROOT, entry)
            print(f"\nScheduled {sc['id']}: {args.cron} {TZ}")

    final = workforce.get_room(ROOT, room["id"])
    print(f"\nRoom '{final['label']}' — {len(final.get('seats') or [])} seats, "
          f"{len(final.get('mcp_services') or [])} connections.")
    print(f"Working folder: library/{workforce.room_folder(ROOT, final)}")
    print("\nIt will run on the schedule. To try it once now, press Run on the "
          "Rooms page — that is several agent runs, so watch the first one before "
          "leaving it to the nightly cron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
