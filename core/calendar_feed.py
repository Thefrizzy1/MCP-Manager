"""core/calendar_feed.py — one calendar to subscribe to.

An iCalendar feed built from what Plutus already knows: every video that went
live (from the channel's uploads), every deadline set on a video, every
workflow the lab published, plus dated entries workers add (script review,
sponsor draft due, planned publish). Calendar apps subscribe to the feed URL
and refresh it themselves.

The URL carries a random token: calendar apps cannot log in, so the token is
the key. Regenerating it cuts off everyone who had the old URL.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core.atomic_json import read_json, write_json

KINDS = ("publish", "deadline", "review", "film", "sponsor", "workflow", "other")


def _path(root: Path) -> Path:
    return Path(root) / "data" / "calendar.json"


def _load(root: Path) -> dict:
    data = read_json(_path(root), {})
    data = data if isinstance(data, dict) else {}
    data.setdefault("events", [])
    return data


def token(root: Path, *, regenerate: bool = False) -> str:
    data = _load(root)
    if regenerate or not data.get("token"):
        data["token"] = secrets.token_urlsafe(24)
        write_json(_path(root), data)
    return data["token"]


def check_token(root: Path, candidate: str) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate, token(root))


def _when(value) -> int:
    """A moment as unix time from an ISO date/time string or a number; 0 if unknown."""
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dtv = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dtv.timestamp())
    except ValueError:
        return 0


def add_event(root: Path, day: str, title: str, description: str = "", link: str = "",
              job: str = "", kind: str = "other", ref: str = "", by: str = "agent",
              evidence: str = "", evidence_at="") -> dict:
    """Add a dated entry, or change one with the same ``ref``.

    Every change is kept in the entry's history with who made it and what it was
    based on. The newest change wins, judged by when its evidence is from: a
    change you make by hand counts from the moment you make it, an agent's
    change counts from the date of the mail (or other source) it read. So an
    agent re-reading an old mail cannot undo a date you moved; a newer mail can.
    Returns the entry, with ``kept`` set when the change was not applied.
    """
    date.fromisoformat(day)                                    # ValueError if malformed
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    now = int(time.time())
    at = now if by == "you" else (_when(evidence_at) or now)
    data = _load(root)
    old = next((e for e in data["events"] if ref and e["id"] == ref), None)
    history = list((old or {}).get("history") or [])
    if old and not history:                    # entries from before history existed
        history = [{"ts": old.get("added", now), "at": old.get("added", now), "date": old["date"],
                    "by": "agent", "evidence": ""}]
    if old:
        current_at = max((h.get("at", 0) for h in history), default=0)
        if at < current_at and day != old["date"]:
            last = max(history, key=lambda h: h.get("at", 0))
            kept = dict(old, kept=f"kept {old['date']}: {'you' if last.get('by') == 'you' else last.get('by')} changed it "
                                  f"based on something newer ({datetime.fromtimestamp(current_at):%d %b %H:%M})")
            history.append({"ts": now, "at": at, "date": day, "by": by, "evidence": evidence[:300], "applied": False})
            kept["history"] = history[-30:]
            data["events"] = [e for e in data["events"] if e["id"] != old["id"]] + [{k: v for k, v in kept.items() if k != "kept"}]
            write_json(_path(root), data)
            return kept
    history.append({"ts": now, "at": at, "date": day, "by": by, "evidence": evidence[:300], "applied": True})
    row = {"id": ref or secrets.token_hex(6), "date": day,
           "title": title.strip()[:200] or (old or {}).get("title", ""),
           "description": (description.strip() or (old or {}).get("description", ""))[:3000],
           "link": (link or (old or {}).get("link", ""))[:500], "job": (job or (old or {}).get("job", ""))[:300],
           "kind": kind if not old or kind != "other" else old.get("kind", kind),
           "added": (old or {}).get("added", now), "history": history[-30:]}
    data["events"] = [e for e in data["events"] if e["id"] != row["id"]] + [row]
    write_json(_path(root), data)
    return row


def move_event(root: Path, event_id: str, day: str, note: str = "") -> dict:
    """You moved an entry by hand. That is the newest truth until newer evidence comes."""
    old = next((e for e in _load(root)["events"] if e["id"] == event_id), None)
    if not old:
        raise KeyError(f"no calendar entry {event_id!r}")
    return add_event(root, day, old["title"], old.get("description", ""), old.get("link", ""), old.get("job", ""),
                     old.get("kind", "other"), event_id, by="you", evidence=note or "moved by hand")


def remove_event(root: Path, event_id: str) -> bool:
    data = _load(root)
    before = len(data["events"])
    data["events"] = [e for e in data["events"] if e["id"] != event_id]
    write_json(_path(root), data)
    return len(data["events"]) < before


def custom_events(root: Path) -> list[dict]:
    return sorted(_load(root)["events"], key=lambda e: e["date"])


def collect(root: Path, jobs: list[dict], uploads: list[dict], lab: dict[str, dict]) -> list[dict]:
    """Every calendar entry, from all sources, oldest first."""
    out: list[dict] = []
    by_video = {j["published"]["id"]: j for j in jobs if j.get("published")}
    for v in uploads:
        if not v.get("published"):
            continue
        job = by_video.get(v["id"])
        views = f"{v['views']:,} views. " if isinstance(v.get("views"), int) else ""
        short = v.get("kind") == "short"
        out.append({"id": f"yt-{v['id']}", "date": v["published"][:10], "kind": "publish",
                    "title": ("Short: " if short else "Published: ") + v["title"],
                    "description": views + (f"Project folder: {job['id']}" if job else ""),
                    "link": f"https://youtu.be/{v['id']}"})
    for j in jobs:
        if j.get("deadline"):
            out.append({"id": f"due-{j['id']}", "date": j["deadline"], "kind": "deadline",
                        "title": f"Due: {j['title']}",
                        "description": f"Currently at: {j['current']}. {j.get('note', '')}".strip(),
                        "link": j.get("publish_url", "")})
    for slug, m in lab.items():
        if m.get("status") == "published" and m.get("updated"):
            out.append({"id": f"wf-{slug}",
                        "date": datetime.fromtimestamp(m["updated"]).date().isoformat(),
                        "kind": "workflow", "title": f"Workflow published: {m.get('name') or slug}",
                        "description": (m.get("review") or {}).get("summary", ""),
                        "link": m.get("pr", "")})
    out += custom_events(root)
    return sorted(out, key=lambda e: e["date"])


def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,") \
        .replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines are at most 75 octets; continuation lines start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur)
            cur = b
        else:
            cur += b
    parts.append(cur)
    return "\r\n ".join(p.decode("utf-8") for p in parts)


def to_ics(events: list[dict], name: str = "Channel") -> str:
    """All-day events as an iCalendar document."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Plutus//Channel calendar//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(name)}",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for e in events:
        try:
            day = date.fromisoformat(e["date"][:10])
        except ValueError:
            continue
        uid = hashlib.sha1(str(e["id"]).encode()).hexdigest()[:20]
        desc = e.get("description", "")
        if e.get("link"):
            desc = f"{desc}\n{e['link']}".strip()
        lines += ["BEGIN:VEVENT", f"UID:{uid}@plutus", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{_esc(e['title'])}", f"CATEGORIES:{_esc(e.get('kind', 'other'))}"]
        if desc:
            lines.append(f"DESCRIPTION:{_esc(desc)}")
        if e.get("link"):
            lines.append(f"URL:{e['link']}")
        lines += ["TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
