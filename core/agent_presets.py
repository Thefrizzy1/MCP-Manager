"""Named kinds of agent: where their work goes, what they can reach, how far they can go.

Three things were being decided by hand on every launch — which connections to
tick, whether writing and posting are allowed, and where the output should land —
and the third was usually not decided at all, so an agent put an hour of research
in its reply and the reply got truncated.

A preset answers all three at once, and the connection list is not just
convenience: it is the tool slice, so picking "data analyst" makes the run
cheaper as well as narrower. See ``core/agent_runner.write_plutus_mcp_config``.

**The prompt block is deliberately tiny.** Four lines, not a manual. A long
standing instruction is one an agent spends tokens re-reading and still ignores
by turn ten; the folder only has to be stated once, concretely, with the tool
that writes to it named. Anything longer competes with the actual task.
"""
from __future__ import annotations

import time
from pathlib import Path

# Placeholders resolved per run, so "weekly research" means *this* week without
# anyone editing the preset.
_TOKENS = {
    "year": lambda t: time.strftime("%Y", t),
    "month": lambda t: time.strftime("%m", t),
    "day": lambda t: time.strftime("%d", t),
    "date": lambda t: time.strftime("%Y-%m-%d", t),
    # ISO week, so the last days of December land in the right year-week pair.
    "week": lambda t: time.strftime("%V", t),
    "quarter": lambda t: f"Q{(int(time.strftime('%m', t)) - 1) // 3 + 1}",
}

DEFAULT_PRESET = "general"

PRESETS: dict[str, dict] = {
    "general": {
        "label": "General",
        "description": "No restrictions beyond what you tick yourself.",
        # None (not []) means "do not narrow the connections" — an empty list
        # would mean "no connections at all", which is a very different run.
        "services": None,
        "folder": "",
        "allow_write": True,
        "allow_publish": False,
        "focus": "",
    },
    "weekly_research": {
        "label": "Weekly research",
        "description": "Reads widely, files findings under this week's folder.",
        "services": ["websearch", "wikipedia", "firecrawl", "hackernews",
                     "reddit", "stackexchange", "agent_db", "filesystem", "nextcloud"],
        "folder": "research/weekly/{year}-W{week}",
        "allow_write": True,
        "allow_publish": False,
        "focus": "Gather and summarise; do not act on what you find.",
    },
    "data_analyst": {
        "label": "Data analyst",
        "description": "Pulls numbers from APIs and interprets them. Reads only.",
        "services": ["public_apis", "currency", "weather", "websearch", "maps",
                     "youtube", "github", "hackernews", "stackexchange", "agent_db"],
        "folder": "analysis/{year}-{month}",
        # The point of this one is that it cannot change anything it measures.
        "allow_write": False,
        "allow_publish": False,
        "focus": ("Pull the underlying numbers rather than someone's summary of them, "
                  "say which endpoint each figure came from, and state plainly when a "
                  "source does not have the data."),
    },
    "homelab_ops": {
        "label": "Homelab ops",
        "description": "Looks after the boxes. Can act, cannot post.",
        "services": ["docker", "omv", "uptime_kuma", "syncthing", "fail2ban",
                     "tailscale", "homeassistant", "ntfy", "agent_db"],
        "folder": "ops/{year}-{month}",
        "allow_write": True,
        "allow_publish": False,
        "focus": "Report what you changed and what you only observed, separately.",
    },
    "writer": {
        "label": "Writer",
        "description": "Turns gathered material into finished text.",
        "services": ["nextcloud", "obsidian", "filesystem", "agent_db",
                     "websearch", "wikipedia"],
        "folder": "drafts/{year}-{month}",
        "allow_write": True,
        "allow_publish": False,
        "focus": "Write the finished piece to a file; do not put it only in your reply.",
    },
}


def preset_names() -> list[str]:
    return list(PRESETS)


def get_preset(name: str | None) -> dict:
    """The named preset, or the unrestricted default. Never raises."""
    return PRESETS.get((name or "").strip(), PRESETS[DEFAULT_PRESET])


def resolve_folder(template: str, when: float | None = None) -> str:
    """``research/weekly/{year}-W{week}`` -> ``research/weekly/2026-W31``.

    An unknown placeholder is left as-is rather than raising: a typo in a preset
    should give a slightly odd folder name, not a failed run.
    """
    if not template:
        return ""
    t = time.localtime(when if when is not None else time.time())
    out = template
    for token, fn in _TOKENS.items():
        out = out.replace("{" + token + "}", fn(t))
    return out.strip("/")


def public_presets() -> list[dict]:
    """What the launch wizard shows, with folders resolved for today."""
    return [{"id": pid,
             "label": p["label"],
             "description": p["description"],
             "folder": resolve_folder(p["folder"]),
             "services": p["services"],
             "allow_write": p["allow_write"],
             "allow_publish": p["allow_publish"]}
            for pid, p in PRESETS.items()]


def preamble(name: str | None, *, root: Path | None = None, when: float | None = None) -> str:
    """The short block prepended to the agent's prompt. Empty for "general".

    Four lines by design. It names one folder and one tool, because that is what
    actually changes behaviour — an agent that is told where to put things puts
    them there, and an agent given a page of filing policy does not.
    """
    p = get_preset(name)
    folder = resolve_folder(p["folder"], when)
    if not folder and not p["focus"]:
        return ""

    lines = []
    if folder:
        _ensure(folder, root)
        lines += [
            "## Where your work goes",
            "",
            f"`{folder}` in the research library. Save anything substantial there with "
            f"`library_write_file` — your reply gets truncated, files do not. If that "
            f"fails, use `db_write_note`, which always works. Somewhere else (Nextcloud, "
            f"Obsidian) only if this task explicitly says so.",
        ]
    if p["focus"]:
        lines += ["", p["focus"]]
    if not p["allow_write"]:
        # Stated rather than left to be discovered by a failed call, which wastes
        # a turn and reads to the model like a broken tool.
        lines += ["", "This run is read-only: every tool that changes anything is "
                       "unavailable. Report what you found; do not try to act on it."]
    return "\n".join(lines).strip()


def _ensure(folder: str, root: Path | None) -> None:
    """Create the folder up front so the agent's first write does not have to."""
    try:
        from core.library import ensure_library, resolve_in_library

        ensure_library(root)
        resolve_in_library(folder, root).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass          # a missing folder is a worse run, not a failed one
