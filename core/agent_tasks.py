"""Agent Playbooks — a library of named, editable research tasks.

Each playbook is a reusable prompt for the headless agent (core/agent_runner.py).
The design that makes the system compound: every research playbook is told to
**read the existing knowledge library first, then add/refine notes** — so each
scheduled run stands on the last and the library gets richer over time.

Playbooks live in data/agent_tasks.json and are seeded with a starter set on
first use. Schedules reference a playbook by id (kind "task"), so editing the
playbook changes what the schedule does — no stale copies.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

# {{LIBRARY}} = the notes folder the agent reads/writes (Obsidian folder or path);
# {{DATE}} = today. Kept small so prompts stay editable and legible.
STARTER_PLAYBOOKS: list[dict] = [
    {
        "id": "competitor-research",
        "name": "Competitor research (YouTube)",
        "description": "Deep-research other AI/ComfyUI YouTube channels in my niche and log what's working.",
        "prompt": (
            "You are a YouTube strategy researcher for the channel @the_frizzy1 "
            "(ComfyUI / local AI image & video, low-VRAM/4GB focus, dark-editorial style).\n\n"
            "1. First, read the existing research library under '{{LIBRARY}}/competitors' "
            "(use the obsidian or filesystem tools) so you build on what's already known and don't repeat it.\n"
            "2. Using web_search and web_fetch, research 3-5 comparable channels (ComfyUI tutorials, "
            "local AI video, Stable Diffusion). For each: recent uploads, topics that are spiking, "
            "titling/thumbnail patterns, roughly how views trend, and any gap I could own.\n"
            "3. Write or update one note per channel under '{{LIBRARY}}/competitors' with dated findings, "
            "and append the 3 strongest content opportunities for me to '{{LIBRARY}}/opportunities.md'.\n"
            "Be concrete and cite source URLs. Today is {{DATE}}."
        ),
    },
    {
        "id": "ai-comfyui-trends",
        "name": "AI & ComfyUI trend scan",
        "description": "Find new models, ComfyUI nodes, and workflows worth covering — especially low-VRAM.",
        "prompt": (
            "Research the latest in local AI image/video generation and ComfyUI, biased toward what runs "
            "on 4-8GB VRAM.\n\n"
            "1. Read '{{LIBRARY}}/ai-trends' first so you extend prior notes instead of repeating.\n"
            "2. With web_search / web_fetch, scan for: new models (HuggingFace/CivitAI), notable ComfyUI "
            "custom nodes and workflows, quantized/GGUF releases, and anything trending on r/comfyui or "
            "r/StableDiffusion in the last week.\n"
            "3. For each promising item, add a dated entry to '{{LIBRARY}}/ai-trends' with: what it is, "
            "VRAM feasibility, why it matters for my audience, a source URL, and a 1-line video angle.\n"
            "Only log real, verifiable finds. Today is {{DATE}}."
        ),
    },
    {
        "id": "channel-audit",
        "name": "My channel audit",
        "description": "Audit my own recent videos against my standards and suggest concrete fixes.",
        "prompt": (
            "Audit the @the_frizzy1 channel against my standards (clear titles, strong hook in the first "
            "15s, chapters, description template with links, consistent thumbnail style).\n\n"
            "1. Read '{{LIBRARY}}/channel' for prior audits and my documented standards.\n"
            "2. Research my recent public videos (web_search/web_fetch on the channel) and, per video, note "
            "what's working and 1-3 specific fixes (title, thumbnail, description, tags, structure).\n"
            "3. Write a dated audit to '{{LIBRARY}}/channel/audit-{{DATE}}.md' with a prioritized fix list.\n"
            "Note: view/CTR/retention analytics need the YouTube API — flag where you're inferring from "
            "public data. Today is {{DATE}}."
        ),
    },
    {
        "id": "script-writer",
        "name": "Script draft from research",
        "description": "Turn the best library findings into a video script draft in my voice.",
        "prompt": (
            "Draft a YouTube video script for @the_frizzy1 in my voice (direct, practical, ComfyUI/low-VRAM, "
            "no fluff).\n\n"
            "1. Read '{{LIBRARY}}/opportunities.md' and '{{LIBRARY}}/ai-trends' and pick the single strongest "
            "topic not already scripted (check '{{LIBRARY}}/scripts').\n"
            "2. Draft: a hook (first 15s), an outline, the full script, 5 title options, and a thumbnail concept.\n"
            "3. Save it to '{{LIBRARY}}/scripts/{{DATE}}-<slug>.md'.\n"
            "Ground every claim in the library notes or a fetched source. Today is {{DATE}}."
        ),
    },
    {
        "id": "weekly-digest",
        "name": "Weekly digest",
        "description": "Summarize everything the research agents added this week into one brief.",
        "prompt": (
            "Summarize the past week of research for @the_frizzy1.\n\n"
            "1. Read the recent dated notes across '{{LIBRARY}}' (competitors, ai-trends, channel, scripts).\n"
            "2. Produce a concise brief: top 3 content opportunities, most important AI/ComfyUI developments, "
            "any channel fixes to make, and what to research next.\n"
            "3. Save it to '{{LIBRARY}}/digests/{{DATE}}-digest.md' and keep it skimmable. Today is {{DATE}}."
        ),
    },
]


def _path(root: Path) -> Path:
    return root / "data" / "agent_tasks.json"


def load_tasks(root: Path) -> list[dict]:
    p = _path(root)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.get("tasks", []) if isinstance(data, dict) else []
        return items if isinstance(items, list) else []
    except Exception:
        return []


def save_tasks(root: Path, tasks: list[dict]) -> None:
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"tasks": tasks}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def seed_if_empty(root: Path) -> list[dict]:
    """Install the starter playbooks the first time, so the feature is useful out of the box."""
    tasks = load_tasks(root)
    if not tasks:
        tasks = [dict(t, created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())) for t in STARTER_PLAYBOOKS]
        save_tasks(root, tasks)
    return tasks


def get_task(root: Path, tid: str) -> dict | None:
    return next((t for t in load_tasks(root) if t.get("id") == tid), None)


def upsert_task(root: Path, entry: dict) -> dict:
    name = (entry.get("name") or "").strip()
    prompt = (entry.get("prompt") or "").strip()
    if not name:
        raise ValueError("Playbook needs a name.")
    if not prompt:
        raise ValueError("Playbook needs a prompt.")
    tasks = load_tasks(root)
    tid = entry.get("id")
    norm = {
        "id": tid or uuid.uuid4().hex[:12],
        "name": name,
        "description": (entry.get("description") or "").strip(),
        "prompt": prompt,
        "created": entry.get("created") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for i, t in enumerate(tasks):
        if t.get("id") == norm["id"]:
            tasks[i] = norm
            save_tasks(root, tasks)
            return norm
    tasks.append(norm)
    save_tasks(root, tasks)
    return norm


def delete_task(root: Path, tid: str) -> bool:
    tasks = load_tasks(root)
    kept = [t for t in tasks if t.get("id") != tid]
    if len(kept) == len(tasks):
        return False
    save_tasks(root, kept)
    return True


def render_prompt(prompt: str, *, library: str, date: str, output_hint: str = "") -> str:
    return (
        prompt.replace("{{LIBRARY}}", library)
        .replace("{{DATE}}", date)
        .replace("{{OUTPUT_HINT}}", output_hint)
    )


# ── AI playbook builder ("build this agent with Claude") ─────────────────────
def build_meta_prompt(description: str, *, library: str, output_hint: str) -> str:
    """A prompt that asks Claude to WRITE a playbook prompt from a description.

    The output is a ready-to-save playbook body — not the research itself.
    """
    return (
        "You are helping design a reusable research/automation agent ('playbook') for a "
        "self-hosted control panel. The agent runs headless on a schedule and can use web "
        "search/fetch plus the operator's homelab tools (media servers, Home Assistant, "
        "Obsidian, filesystem, Docker, etc.).\n\n"
        f"Knowledge is persisted under the folder `{library}`. {output_hint}\n\n"
        "Write a clear, effective PROMPT for this agent based on the request below. Rules for "
        "the prompt you write:\n"
        "- Start by telling the agent to READ the existing library notes first so it builds on "
        "prior work instead of repeating it.\n"
        "- Give numbered, concrete steps; require citing source URLs for any claim.\n"
        "- Tell it to write/append dated notes back into the library so knowledge compounds.\n"
        "- Use the placeholders {{LIBRARY}} (the folder), {{OUTPUT_HINT}} (how to persist), and "
        "{{DATE}} (today) verbatim where relevant.\n"
        "- Keep it focused and free of preamble.\n\n"
        "Output ONLY the finished prompt text — no explanation, no code fences.\n\n"
        f"Request: {description.strip()}"
    )
