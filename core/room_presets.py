"""Pre-made rooms: a floor that already works, instead of an empty one.

Creating a room is easy. Creating a room that produces something worth reading is
not — it needs a brief that says what "done" looks like, seats in an order where
each one has something to react to, and connections narrow enough that the tool
list does not drown the task. Getting all three right is a half-hour of fiddling
that every user has to repeat, so the good versions ship here.

**A preset is a whole pipeline, not one room.** The interesting unit is
research → office → publishing: gather, then decide what it means, then write the
thing that leaves the building. Installing one creates every room and wires the
chain, so the handoff — shared working folder, last room's output as the next
one's inbox — exists from the first run.

**The briefs are the product.** Each seat's goal names what it must produce and
what it must not do, because the failure mode of a room is not a seat that fails,
it is five seats that all write the same summary in slightly different words. The
manager and reviewer seats exist to break that: they are told to redirect rather
than redo, which is what ``room_advise`` is for.

Every seat is staffed with the same provider account at install time and can be
re-pointed afterwards — picking a model per seat is a decision you make once you
know what the room costs, not before it has ever run.
"""
from __future__ import annotations

from pathlib import Path

# A small fixed palette. Rooms carry a colour so a floor of a dozen reads at a
# glance; free-form hex would let a user pick something invisible on one theme,
# so the UI maps these names to tokens that work on both.
COLOURS: tuple[str, ...] = ("slate", "indigo", "violet", "teal", "amber", "rose", "lime")
DEFAULT_COLOUR = "slate"


def valid_colour(value: str) -> str:
    """A colour from the palette, or the default. Never raises — a bad colour is
    a slightly duller room, not a failed save."""
    v = (value or "").strip().lower()
    return v if v in COLOURS else DEFAULT_COLOUR


# Connections a room is given. Deliberately narrow: the tool manifest is the
# dominant per-request cost, and a researcher holding the Docker tools is paying
# for them on every single turn.
_READING = ["websearch", "wikipedia", "firecrawl", "hackernews", "reddit",
            "stackexchange", "github", "agent_db"]
_THINKING = ["agent_db", "websearch", "wikipedia"]
_PUBLISHING = ["nextcloud", "obsidian", "agent_db", "reddit", "ntfy"]


PRESETS: dict[str, dict] = {
    "research_pipeline": {
        "label": "Research pipeline",
        "description": "Research → Office → Publishing. Gather, decide what it "
                       "means, then write the thing that leaves the building.",
        "rooms": [
            {
                "label": "Research",
                "colour": "indigo",
                "services": _READING,
                "brief": (
                    "Go deep on the topic in this brief. Depth means primary sources, "
                    "disagreement, and dates — not a wider sweep of the same summaries.\n\n"
                    "Rules for this room:\n"
                    "- Every claim carries a source URL and the date it was published. "
                    "An undated claim is a rumour; mark it as one.\n"
                    "- Where sources disagree, record the disagreement rather than "
                    "picking the more popular side.\n"
                    "- Say plainly what you could NOT find. A gap you name is useful; "
                    "a gap you paper over is a defect the next room inherits.\n"
                    "- Write findings to files in your working folder as you go. Your "
                    "reply is truncated before the next room sees it; files are not."
                ),
                "seats": [
                    {"role": "researcher", "label": "Scout",
                     "goal": "Map the territory first: who the credible sources are, "
                             "what the main positions are, and which questions actually "
                             "need answering. Write it as `scope.md`. Do not answer them yet."},
                    {"role": "researcher", "label": "Deep researcher",
                     "goal": "Answer the questions in `scope.md` one at a time, with "
                             "sources and dates. One file per question. Prefer primary "
                             "sources — the paper, the changelog, the docs — over "
                             "someone's write-up of them."},
                    {"role": "reviewer", "label": "Fact checker",
                     "goal": "Check each claim against its cited source. List anything "
                             "unsupported, out of date, or contradicted elsewhere, most "
                             "serious first. If the scope itself was wrong, call "
                             "room_advise — do not quietly rewrite the research."},
                ],
            },
            {
                "label": "Office",
                "colour": "amber",
                "services": _THINKING,
                "brief": (
                    "The research room has handed you its files. Your job is judgement, "
                    "not more gathering.\n\n"
                    "Produce a decision, not a longer summary: what does this material "
                    "actually support, what would you act on, and what is still too thin "
                    "to act on. Read the files in the working folder — the handoff text "
                    "is only an excerpt."
                ),
                "seats": [
                    {"role": "manager", "label": "Analyst",
                     "goal": "Read every file in the working folder and write "
                             "`analysis.md`: what the evidence supports, what it does "
                             "not, and the two or three things that actually matter. "
                             "Name the strongest counter-argument to your own reading."},
                    {"role": "reviewer", "label": "Devil's advocate",
                     "goal": "Argue against `analysis.md` as hard as the evidence "
                             "allows. Where the analysis is right, say so and stop — a "
                             "manufactured objection is worse than none."},
                ],
            },
            {
                "label": "Publishing",
                "colour": "teal",
                "services": _PUBLISHING,
                "brief": (
                    "Turn the analysis into the finished piece the original brief asked "
                    "for. This is the room whose output a person reads.\n\n"
                    "Write it to a file. Lead with the conclusion, keep the evidence "
                    "underneath it, and keep every source link — a reader who cannot "
                    "check you will not trust you. Do not post anything publicly unless "
                    "the brief explicitly says to."
                ),
                "seats": [
                    {"role": "writer", "label": "Writer",
                     "goal": "Write the finished piece to a file in the working folder. "
                             "Conclusion first, evidence under it, sources kept. Match "
                             "the length and format the brief asked for."},
                    {"role": "reviewer", "label": "Editor",
                     "goal": "Edit for a reader who has not seen any of the research. "
                             "Cut what does not earn its place, fix anything the "
                             "evidence does not support, and leave the file publishable."},
                ],
            },
        ],
    },

    "watchtower": {
        "label": "Watchtower",
        "description": "One room, scheduled. Sweeps your sources and reports only "
                       "what changed since last time.",
        "rooms": [
            {
                "label": "Watchtower",
                "colour": "violet",
                "services": _READING + ["ntfy"],
                "brief": (
                    "Sweep the sources in this brief and report what is NEW since the "
                    "last run. Read the previous run's file in your working folder "
                    "first — if you cannot tell what changed, say so rather than "
                    "reporting everything again.\n\n"
                    "A watch report that repeats last week's is worse than no report: "
                    "it trains the reader to stop opening them. Short is correct when "
                    "little happened."
                ),
                "seats": [
                    {"role": "researcher", "label": "Watcher",
                     "goal": "Check each source, compare against the previous run's "
                             "file, and write `<date>.md` with only what changed. "
                             "Nothing changed is a valid and useful answer."},
                    {"role": "reviewer", "label": "Filter",
                     "goal": "Cut anything that is not genuinely new or does not matter "
                             "to the brief. Leave the shortest report that is still "
                             "complete."},
                ],
            },
        ],
    },

    "build_squad": {
        "label": "Build squad",
        "description": "Research a change, make it, then review it. For work on a "
                       "codebase rather than a document.",
        "rooms": [
            {
                "label": "Spec",
                "colour": "lime",
                "services": ["github", "websearch", "stackexchange", "filesystem", "agent_db"],
                "brief": (
                    "Work out what should actually be built, before anyone builds it.\n\n"
                    "Read the existing code first. A spec written without reading the "
                    "code specifies a system that does not exist. State what changes, "
                    "what stays, and how you would know it worked."
                ),
                "seats": [
                    {"role": "researcher", "label": "Codebase reader",
                     "goal": "Read the relevant code and write `current.md`: how it "
                             "works today, and which files a change would touch. Quote "
                             "real paths and symbols, not paraphrases."},
                    {"role": "manager", "label": "Spec writer",
                     "goal": "Write `spec.md` from `current.md`: the change, its "
                             "blast radius, and the test that proves it. If the request "
                             "does not survive contact with the code, call room_advise."},
                ],
            },
            {
                "label": "Build",
                "colour": "rose",
                "services": ["github", "filesystem", "agent_db", "docker"],
                "brief": (
                    "Implement `spec.md`. Nothing else — scope creep in an agent is "
                    "indistinguishable from a bug.\n\n"
                    "Say exactly what you changed and what you deliberately did not. "
                    "If something in the spec turns out to be wrong, call room_advise "
                    "rather than silently building something different."
                ),
                "seats": [
                    {"role": "developer", "label": "Developer",
                     "goal": "Implement the spec and write `changes.md` listing every "
                             "file touched and why."},
                    {"role": "reviewer", "label": "Reviewer",
                     "goal": "Review the change against `spec.md`. List concrete "
                             "defects, most serious first, each with the file and the "
                             "failing case. Approve only if you would ship it."},
                ],
            },
        ],
    },
}


def public_presets() -> list[dict]:
    """What the Rooms page offers, without the full prompt text.

    The briefs run to several hundred words each; a picker does not want them,
    and sending them to render a three-line card is pure weight on every poll.
    """
    return [{
        "id": pid,
        "label": p["label"],
        "description": p["description"],
        "rooms": [{"label": r["label"], "colour": r["colour"],
                   "seats": len(r["seats"]),
                   "roles": [s["role"] for s in r["seats"]]}
                  for r in p["rooms"]],
    } for pid, p in PRESETS.items()]


def install(root: Path, preset_id: str, *, provider: str, account_id: str,
            model: str = "") -> list[dict]:
    """Create a preset's rooms, staff them, and wire the chain. Returns the rooms.

    Installing the same preset twice is allowed and gives independent rooms —
    "Research", then "Research 2" — because the alternative is an error on the
    one action a user is most likely to repeat while trying things out.

    Not transactional by design: if the third room fails to save, the first two
    are still real rooms with real seats, which is a better outcome than silently
    rolling back work the user can see on the floor.
    """
    from core import workforce

    preset = PRESETS.get((preset_id or "").strip())
    if not preset:
        raise KeyError(f"no room preset '{preset_id}'")
    if not provider or not account_id:
        raise ValueError("a preset needs a provider account to staff its seats with")

    existing = {r.get("label", "").lower() for r in workforce.load_rooms(root)}
    created: list[dict] = []
    for spec in preset["rooms"]:
        label = _free_label(spec["label"], existing)
        existing.add(label.lower())
        room = workforce.add_room(root, label, mcp_services=list(spec["services"]))
        workforce.update_room(root, room["id"], {"brief": spec["brief"],
                                                 "colour": valid_colour(spec["colour"])})
        for seat in spec["seats"]:
            workforce.add_seat(root, room["id"], role=seat["role"], provider=provider,
                               account_id=account_id, label=seat["label"],
                               goal=seat["goal"], model=model)
        created.append(room)

    # Chain them in the order they are declared — that order *is* the pipeline.
    for a, b in zip(created, created[1:]):
        workforce.update_room(root, a["id"], {"next_room": b["id"]})

    return [workforce.get_room(root, r["id"]) or r for r in created]


def _free_label(label: str, taken: set[str]) -> str:
    if label.lower() not in taken:
        return label
    for n in range(2, 100):
        candidate = f"{label} {n}"
        if candidate.lower() not in taken:
            return candidate
    return f"{label} {len(taken)}"
