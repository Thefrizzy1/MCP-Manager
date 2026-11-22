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


def add_event(root: Path, day: str, title: str, description: str = "", link: str = "",
              job: str = "", kind: str = "other", ref: str = "") -> dict:
    """Add a dated entry. The same ``ref`` again replaces the earlier entry."""
    date.fromisoformat(day)                                    # ValueError if malformed
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    data = _load(root)
    row = {"id": ref or secrets.token_hex(6), "date": day, "title": title.strip()[:200],
           "description": description.strip()[:3000], "link": link[:500], "job": job[:300],
           "kind": kind, "added": int(time.time())}
    data["events"] = [e for e in data["events"] if e["id"] != row["id"]] + [row]
    write_json(_path(root), data)
    return row


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
    return (text or "").replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,") \
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
