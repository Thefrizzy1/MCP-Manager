"""core/prompter.py — scripts for the teleprompter, and the link between its screen and its controls.

A script is either a document in a video folder or a file the user uploaded
(.md, .txt, .docx or .pdf). Uploads are kept as text in ``data/prompter``.
The screen (the tablet) waits for commands; any other signed-in browser can send
them, so a remote paired to the Mac works as well as one paired to the tablet.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import deque
from pathlib import Path

from core import doc_text
from core.atomic_json import read_json, write_json

MAX_UPLOAD = 20 * 1024 * 1024
MAX_TEXT = 400_000
COMMANDS = ("toggle", "play", "pause", "next", "back", "restart", "faster", "slower",
            "bigger", "smaller", "mirror", "flip", "load")
_ID = re.compile(r"^[0-9a-f]{12}$")


def _dir(root: Path) -> Path:
    return Path(root) / "data" / "prompter"


# ── text ────────────────────────────────────────────────────────────────────
def unwrap_pdf(text: str) -> str:
    """PDF text breaks at the page's line width; put the sentences back together."""
    text = re.sub(r"(?m)^===== Page \d+ / \d+ =====$", "", text)
    text = re.sub(r"(?m)^\s*(?:page\s+)?\d{1,3}(?:\s*(?:/|of)\s*\d{1,3})?\s*$", "", text, flags=re.I)
    text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        prev = out[-1] if out else ""
        starts_item = bool(re.match(r"([-*•#]|\d+[.)])\s", line))
        ended = bool(re.search(r"[.!?:…][\"'”’)\]]*$", prev))
        # A break after a sentence is kept; a break in the middle of one is joined.
        joins = bool(line and prev) and not starts_item and not prev.startswith("#") and not ended \
            and (line[:1].islower() or len(prev) > 45)
        if joins:
            out[-1] = f"{prev} {line}"
        else:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _plain(line: str) -> str:
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"`([^`]*)`", r"\1", line)
    line = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", line)
    line = re.sub(r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", line)
    line = re.sub(r"^\s*>\s?", "", line)
    return re.sub(r"^(\s*)[*+-]\s+", r"\1", line).rstrip()


def sections(text: str) -> list[dict]:
    """Headings split the script into parts; Markdown marks are taken off the spoken text."""
    text = re.sub(r"\A---\n.*?\n---\n", "", text.replace("\r\n", "\n"), flags=re.S)
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    out: list[dict] = []
    cur: dict = {"title": "", "lines": []}

    def close() -> None:
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(cur["lines"])).strip()
        if cur["title"] or body:
            out.append({"title": cur["title"], "text": body})

    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            close()
            cur = {"title": _plain(m[1]).strip(" #"), "lines": []}
        elif re.match(r"^\s*(```|~~~|([-*_]\s*){3,}$)", line):
            cur["lines"].append("")
        else:
            cur["lines"].append(_plain(line))
    close()
    return out


# ── uploads ─────────────────────────────────────────────────────────────────
def save_upload(root: Path, filename: str, data: bytes, job: str = "") -> dict:
    if len(data) > MAX_UPLOAD:
        raise ValueError("the file is over 20 MB")
    kind = doc_text.kind_of(filename, "", data)
    if kind not in ("pdf", "docx", "text"):
        raise ValueError("upload a .md, .txt, .docx or .pdf file")
    try:
        got = doc_text.extract(filename, "", data, max_chars=MAX_TEXT)
    except RuntimeError as e:
        raise ValueError(str(e)) from e
    if kind == "pdf" and any(n.startswith("No page has a text layer") for n in got["notes"]):
        raise ValueError("this PDF is a scan or picture, so there is no text in it to read")
    text = got["text"]
    if kind == "pdf":
        text = unwrap_pdf(re.sub(r"\[no text layer on this page[^\]]*\]", "", text))
    if not text.strip():
        raise ValueError("there is no text in this file")
    row = {"id": uuid.uuid4().hex[:12], "name": Path(filename.replace("\\", "/")).name[:160], "job": job[:300],
           "kind": kind, "ts": int(time.time()), "text": text,
           "note": "The end was cut off: the file is very long." if got["truncated"] else ""}
    _dir(root).mkdir(parents=True, exist_ok=True)
    write_json(_dir(root) / f"{row['id']}.json", row)
    return {k: v for k, v in row.items() if k != "text"}


def uploads(root: Path) -> list[dict]:
    rows = [read_json(p, {}) for p in _dir(root).glob("*.json") if _ID.match(p.stem)]
    rows = [{k: v for k, v in r.items() if k != "text"} for r in rows if r.get("id")]
    return sorted(rows, key=lambda r: -r.get("ts", 0))


def upload(root: Path, uid: str) -> dict:
    row = read_json(_dir(root) / f"{uid}.json", {}) if _ID.match(uid or "") else {}
    if not row:
        raise KeyError("that script is not here any more")
    return row


def remove_upload(root: Path, uid: str) -> bool:
    path = _dir(root) / f"{uid}.json"
    if not _ID.match(uid or "") or not path.is_file():
        return False
    path.unlink()
    return True


# ── the screen and its controls ─────────────────────────────────────────────
class Link:
    """Commands on their way to the screen, and the last thing the screen reported."""

    def __init__(self) -> None:
        self.seq = int(time.time() * 1000)      # keeps rising over a restart
        self.queue: deque[dict] = deque(maxlen=50)
        self.status: dict = {}
        self._waiting: set[asyncio.Event] = set()

    def send(self, cmd: str, source: str = "") -> int:
        if cmd not in COMMANDS:
            raise ValueError(f"unknown command {cmd!r}")
        self.seq += 1
        self.queue.append({"seq": self.seq, "cmd": cmd, "source": source, "ts": time.time()})
        for ev in self._waiting:
            ev.set()
        return self.seq

    async def wait(self, after: int, timeout: float = 20.0) -> dict:
        """Commands after ``after``; a screen that just opened (-1) only gets the position."""
        if after < 0:
            return {"seq": self.seq, "commands": []}
        if self.seq <= after:
            ev = asyncio.Event()
            self._waiting.add(ev)
            try:
                await asyncio.wait_for(ev.wait(), timeout)
            except asyncio.TimeoutError:
                pass
            finally:
                self._waiting.discard(ev)
        # A press from before a dropped connection must not fire minutes later.
        fresh = [c for c in self.queue if c["seq"] > after and (c["cmd"] == "load" or time.time() - c["ts"] < 10)]
        return {"seq": self.seq, "commands": fresh}

    def report(self, status: dict) -> None:
        self.status = {**status, "ts": time.time()}

    def screen(self) -> dict:
        on = time.time() - self.status.get("ts", 0) < 12
        return {**self.status, "on": on}


def current(root: Path) -> str:
    return str(read_json(_dir(root) / "current.json", {}).get("source") or "")


def set_current(root: Path, source: str) -> None:
    _dir(root).mkdir(parents=True, exist_ok=True)
    write_json(_dir(root) / "current.json", {"source": source})
