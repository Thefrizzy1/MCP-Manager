"""core/studio.py — the production studio's working memory.

Three durable things, in one SQLite file (``data/studio.db``):

  tasks     a piece of work for one agent on one video (or the channel): research,
            script, thumbnail concepts, edit plan, shorts plan, upload pack, a chat
            message to answer. A task can wait for another task, can be found not
            required (with the reason), and survives restarts.
  messages  the chat threads: one per video plus one for the whole channel.
  runs      every time an agent worked: on what, where, how long, how it ended.
            This is each agent's timeline.

A dispatcher on a machine with a Claude login asks for the next task its agents
can do, runs the session and reports back. Nothing here starts a session itself.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

# kind of work -> the agent that does it
KIND_AGENT = {
    "intake": "manager", "chat": "manager",
    "research": "researcher", "script": "writer",
    "thumbnail": "thumbnail", "edit_plan": "editor", "motion": "motion",
    "shorts": "shorts", "upload_pack": "publisher",
}
OPEN = ("queued", "running")
CLOSED_OK = ("done", "not_required", "approved")
STATUSES = OPEN + CLOSED_OK + ("failed", "cancelled")
CHANNEL = "channel"                     # thread / job id for talk about the whole channel
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, kind TEXT NOT NULL, agent TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', instruction TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '', after INTEGER, machine TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL, started INTEGER NOT NULL DEFAULT 0, finished INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS tasks_job ON tasks(job);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, thread TEXT NOT NULL, role TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL, ts INTEGER NOT NULL, task_id INTEGER);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread, id);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, machine TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '',
  job TEXT NOT NULL DEFAULT '', started INTEGER NOT NULL, finished INTEGER NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '', task_id INTEGER);
CREATE INDEX IF NOT EXISTS runs_agent ON runs(agent, id);
"""


def _connect(root: Path) -> sqlite3.Connection:
    path = Path(root) / "data" / "studio.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=20)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _row(r) -> dict:
    return dict(r) if r is not None else {}


# ── tasks ────────────────────────────────────────────────────────────────────
def add_task(root: Path, job: str, kind: str, instruction: str = "", after: int | None = None,
             status: str = "queued", reason: str = "") -> dict:
    """Give an agent work on a video. An open task of the same kind for the same
    video is returned instead of adding a second one (chat messages excepted)."""
    if kind not in KIND_AGENT:
        raise ValueError(f"kind must be one of {', '.join(KIND_AGENT)}")
    if status not in ("queued", "not_required"):
        raise ValueError("a new task is queued or not_required")
    now = int(time.time())
    with _LOCK, _connect(root) as con:
        if kind != "chat":
            cur = con.execute("SELECT * FROM tasks WHERE job=? AND kind=? AND status IN ('queued','running')",
                              (job, kind)).fetchone()
            if cur:
                return _row(cur)
        c = con.execute("INSERT INTO tasks(job,kind,agent,status,instruction,reason,after,created,finished) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (job, kind, KIND_AGENT[kind], status, instruction[:20000], reason[:1000], after, now,
                         now if status == "not_required" else 0))
        return _row(con.execute("SELECT * FROM tasks WHERE id=?", (c.lastrowid,)).fetchone())


def next_task(root: Path, agents: list[str], machine: str = "") -> dict:
    """The oldest queued task one of these agents can start now, marked running.

    A task that waits for another starts only once that one ended well (done,
    not required or approved). If the one it waits for failed, it stays queued:
    it is shown as blocked until someone retries or cancels.
    """
    if not agents:
        return {}
    now = int(time.time())
    with _LOCK, _connect(root) as con:
        marks = ",".join("?" * len(agents))
        rows = con.execute(f"SELECT * FROM tasks WHERE status='queued' AND agent IN ({marks}) ORDER BY "
                           "CASE kind WHEN 'chat' THEN 0 WHEN 'intake' THEN 1 ELSE 2 END, id", agents).fetchall()
        for r in rows:
            if r["after"]:
                dep = con.execute("SELECT status FROM tasks WHERE id=?", (r["after"],)).fetchone()
                if dep and dep["status"] not in CLOSED_OK:
                    continue
            con.execute("UPDATE tasks SET status='running', started=?, machine=? WHERE id=? AND status='queued'",
                        (now, machine, r["id"]))
            con.execute("INSERT INTO runs(agent,machine,kind,job,started,task_id) VALUES(?,?,?,?,?,?)",
                        (r["agent"], machine, r["kind"], r["job"], now, r["id"]))
            return _row(con.execute("SELECT * FROM tasks WHERE id=?", (r["id"],)).fetchone())
    return {}


def finish_task(root: Path, task_id: int, status: str, result: str = "", reason: str = "") -> dict:
    if status not in ("done", "failed", "not_required", "queued"):
        raise ValueError("status must be done, failed, not_required, or queued (to hand it back)")
    now = int(time.time())
    with _LOCK, _connect(root) as con:
        t = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            raise KeyError(f"no task {task_id}")
        back = status == "queued"                 # handed back untouched (e.g. usage limit)
        con.execute("UPDATE tasks SET status=?, result=?, reason=?, finished=?, started=? WHERE id=?",
                    (status, result[:20000], reason[:1000], 0 if back else now, 0 if back else t["started"], task_id))
        con.execute("UPDATE runs SET finished=?, ok=?, text=? WHERE task_id=? AND finished=0",
                    (now, 1 if status in ("done", "not_required") else 0,
                     ("paused: " + reason if back else (result or reason))[:400], task_id))
        return _row(con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def set_status(root: Path, task_id: int, status: str) -> dict:
    """Dashboard: retry (queued), cancel, or approve a task by hand."""
    if status not in ("queued", "cancelled", "approved"):
        raise ValueError("status must be queued, cancelled or approved")
    with _LOCK, _connect(root) as con:
        con.execute("UPDATE tasks SET status=?, finished=? WHERE id=?",
                    (status, 0 if status == "queued" else int(time.time()), task_id))
        return _row(con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def tasks(root: Path, job: str = "", open_only: bool = False, limit: int = 300) -> list[dict]:
    q, args = "SELECT * FROM tasks", []
    where = []
    if job:
        where.append("job=?")
        args.append(job)
    if open_only:
        where.append("status IN ('queued','running')")
    if where:
        q += " WHERE " + " AND ".join(where)
    with _LOCK, _connect(root) as con:
        rows = [_row(r) for r in con.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()]
        by_id = {r["id"]: r for r in rows}
        for r in rows:                              # a queued task whose dependency failed is blocked
            dep = by_id.get(r["after"]) if r["after"] else None
            if r["status"] == "queued" and r["after"] and dep is None:
                d = con.execute("SELECT status FROM tasks WHERE id=?", (r["after"],)).fetchone()
                dep = _row(d)
            r["blocked"] = bool(r["status"] == "queued" and dep and dep.get("status") in ("failed", "cancelled"))
            r["waiting"] = bool(r["status"] == "queued" and dep and dep.get("status") in OPEN)
    return rows


def by_job(root: Path) -> dict[str, list[dict]]:
    """Recent tasks grouped by video: what the board needs to show work and skips."""
    out: dict[str, list[dict]] = {}
    for t in tasks(root, limit=1500):
        out.setdefault(t["job"], []).append(t)
    return out


# ── chat ─────────────────────────────────────────────────────────────────────
def post(root: Path, thread: str, role: str, text: str, agent: str = "", task_id: int | None = None) -> dict:
    if role not in ("user", "agent", "system"):
        raise ValueError("role must be user, agent or system")
    with _LOCK, _connect(root) as con:
        c = con.execute("INSERT INTO messages(thread,role,agent,text,ts,task_id) VALUES(?,?,?,?,?,?)",
                        (thread, role, agent, text.strip()[:20000], int(time.time()), task_id))
        return _row(con.execute("SELECT * FROM messages WHERE id=?", (c.lastrowid,)).fetchone())


def thread(root: Path, thread_id: str, limit: int = 80) -> list[dict]:
    with _LOCK, _connect(root) as con:
        rows = con.execute("SELECT * FROM messages WHERE thread=? ORDER BY id DESC LIMIT ?", (thread_id, limit)).fetchall()
    return [_row(r) for r in reversed(rows)]


# ── agent timelines ──────────────────────────────────────────────────────────
def start_run(root: Path, agent: str, machine: str = "", kind: str = "", job: str = "") -> int:
    """For work that is not a task (a scheduled round): open a run, close it with end_run."""
    with _LOCK, _connect(root) as con:
        return con.execute("INSERT INTO runs(agent,machine,kind,job,started) VALUES(?,?,?,?,?)",
                           (agent, machine, kind, job, int(time.time()))).lastrowid


def end_run(root: Path, run_id: int, ok: bool, text: str = "") -> None:
    with _LOCK, _connect(root) as con:
        con.execute("UPDATE runs SET finished=?, ok=?, text=? WHERE id=?",
                    (int(time.time()), 1 if ok else 0, text[:400], run_id))


def mark_round(root: Path, agent: str, busy: bool, text: str = "", machine: str = "") -> None:
    """A scheduled round reporting in: the first busy report opens a run, the
    report that it is done closes it. Gives rounds a place on the agent's timeline."""
    now = int(time.time())
    with _LOCK, _connect(root) as con:
        cur = con.execute("SELECT id FROM runs WHERE agent=? AND finished=0 AND task_id IS NULL ORDER BY id DESC LIMIT 1",
                          (agent,)).fetchone()
        if busy and not cur:
            con.execute("INSERT INTO runs(agent,machine,kind,job,started,text) VALUES(?,?,?,?,?,?)",
                        (agent, machine, "round", "", now, text[:400]))
        elif busy and cur:
            con.execute("UPDATE runs SET text=? WHERE id=?", (text[:400], cur["id"]))
        elif cur:
            con.execute("UPDATE runs SET finished=?, ok=?, text=? WHERE id=?",
                        (now, 0 if text.lower().startswith(("paused", "shift ended without")) else 1, text[:400], cur["id"]))


def runs(root: Path, agent: str = "", limit: int = 40) -> list[dict]:
    q, args = "SELECT * FROM runs", []
    if agent:
        q += " WHERE agent=?"
        args.append(agent)
    with _LOCK, _connect(root) as con:
        return [_row(r) for r in con.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()]


def agent_states(root: Path, stale_after: int = 4 * 3600) -> dict[str, dict]:
    """Per agent: what it is running now (and where), its queue, and its last finished run."""
    now = int(time.time())
    out: dict[str, dict] = {}
    with _LOCK, _connect(root) as con:
        for r in con.execute("SELECT * FROM runs WHERE finished=0 AND started>? ORDER BY id", (now - stale_after,)):
            out.setdefault(r["agent"], {})["now"] = _row(r)
        for r in con.execute("SELECT agent, COUNT(*) n FROM tasks WHERE status='queued' GROUP BY agent"):
            out.setdefault(r["agent"], {})["queued"] = r["n"]
        for r in con.execute("SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs WHERE finished>0 GROUP BY agent)"):
            out.setdefault(r["agent"], {})["last"] = _row(r)
    return out


# ── what makes sense to do next on a video ───────────────────────────────────
def actions(job: dict, job_tasks: list[dict]) -> list[dict]:
    """The few things that make sense for this video right now. Work an agent
    already has in hand is not offered again; finished things are not offered."""
    st = {s["id"]: s["status"] for s in job["stages"]}
    busy = {t["kind"] for t in job_tasks if t["status"] in OPEN}
    done = lambda s: st.get(s) in ("done", "skipped")
    out: list[dict] = []

    def offer(kind: str, label: str) -> None:
        if kind not in busy:
            out.append({"id": kind, "label": label})

    if not job.get("script") and not done("script"):
        if st.get("research") == "todo":
            offer("research", "Research")
        offer("script", "Write script")
    if job.get("script") and not done("film"):
        out.append({"id": "film", "label": "Film", "route": "film"})
    if st.get("film") in ("active", "done") and not done("edit"):
        offer("edit_plan", "Start edit")
        offer("motion", "Plan graphics")
    if not job.get("thumbnails") and (job.get("script") or st.get("film") != "todo"):
        offer("thumbnail", "Draft a thumbnail")
    if done("edit") and not done("packaging") and not done("publish"):
        offer("upload_pack", "Prepare upload")
    if st.get("publish") == "done" and st.get("shorts") == "todo":
        offer("shorts", "Make Shorts")
    return out


def stage_marks(root: Path) -> dict[str, dict[str, str]]:
    """Stages that tasks settle beyond what the files show: research found not
    required is 'skipped' (shown crossed out, and it does not hold the video up)."""
    out: dict[str, dict[str, str]] = {}
    with _LOCK, _connect(root) as con:
        for r in con.execute("SELECT job, kind FROM tasks WHERE status='not_required' AND kind IN ('research','script')"):
            out.setdefault(r["job"], {})[r["kind"]] = "skipped"
    return out
