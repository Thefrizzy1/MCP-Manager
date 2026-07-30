"""
tools/agent_db.py — the always-available writable store.

Exposed so an agent always has somewhere to put its output, even when Nextcloud,
Obsidian and the filesystem allow-list are all misconfigured. These tools are in
ALWAYS_EXPOSED, so the tool slicer cannot remove them — the failure that left an
agent announcing "I don't have tools to write files" with nowhere to save an hour
of research.

Every write returns the row read back from the database, so the model can state
the note was saved because it has the stored record, not because the call did not
raise.
"""
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from core import agent_db

_ROOT = Path(__file__).resolve().parents[1]


def register_agent_db_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class WriteInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        title: str = Field(..., description="Short title for the note", min_length=1, max_length=300)
        body: str = Field(default="", description="Full note content (markdown is fine)", max_length=200_000)
        tags: str = Field(default="", description="Comma-separated tags", max_length=300)
        note_id: Optional[int] = Field(default=None, description="Update this note instead of creating one")

    @mcp.tool(name="db_write_note", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def db_write_note(params: WriteInput) -> str:
        """Save a note to Plutus's own database. Always available.

        Use this whenever another destination (Nextcloud, Obsidian, the filesystem)
        is unavailable or fails — research must never be lost for want of somewhere
        to put it. Returns the stored record, including the id needed to read or
        update it later.
        """
        try:
            tags = [t for t in (params.tags or "").split(",") if t.strip()]
            rec = agent_db.write_note(_ROOT, params.title, params.body, tags, params.note_id)
        except KeyError as e:
            return f"Error: {e}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:  # surfaced, not swallowed — a silent failure here loses work
            return f"Error: could not write to the agent database: {e}"
        return (f"✓ Saved to the agent database and read back.\n\n"
                f"- id: `{rec['id']}`\n- title: {rec['title']}\n"
                f"- tags: {', '.join(rec['tags']) or '(none)'}\n"
                f"- bytes stored: {len(rec['body'])}\n\n"
                f"Read it again with db_read_note(note_id={rec['id']}).")

    class ReadInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        note_id: int = Field(..., description="Note id from db_write_note / db_list_notes", ge=1)

    @mcp.tool(name="db_read_note", annotations={"readOnlyHint": True})
    async def db_read_note(params: ReadInput) -> str:
        """Read one note back out of the agent database."""
        rec = agent_db.read_note(_ROOT, params.note_id)
        if not rec:
            return f"No note with id {params.note_id}."
        tags = ", ".join(rec["tags"]) or "(none)"
        return f"## {rec['title']}\n\n_id {rec['id']} · tags: {tags}_\n\n{rec['body']}"

    class ListInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        limit: int = Field(default=20, description="How many to list", ge=1, le=200)

    @mcp.tool(name="db_list_notes", annotations={"readOnlyHint": True})
    async def db_list_notes(params: ListInput) -> str:
        """List the most recent notes in the agent database."""
        rows = agent_db.list_notes(_ROOT, params.limit)
        if not rows:
            return "The agent database is empty."
        out = [f"## Agent database ({len(rows)} most recent)\n"]
        for r in rows:
            tags = f" · {', '.join(r['tags'])}" if r["tags"] else ""
            out.append(f"- `{r['id']}` **{r['title']}**{tags} ({len(r['body'])} chars)")
        return "\n".join(out)

    class SearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Text to look for in titles, bodies and tags", min_length=1, max_length=200)
        limit: int = Field(default=20, ge=1, le=200)

    @mcp.tool(name="db_search_notes", annotations={"readOnlyHint": True})
    async def db_search_notes(params: SearchInput) -> str:
        """Search the agent database by title, body or tag."""
        rows = agent_db.search_notes(_ROOT, params.query, params.limit)
        if not rows:
            return f"Nothing in the agent database matches '{params.query}'."
        out = [f"## Search: '{params.query}' ({len(rows)} hits)\n"]
        for r in rows:
            out.append(f"- `{r['id']}` **{r['title']}** — {r['body'][:120].strip()}…")
        return "\n".join(out)

    class DeleteInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        note_id: int = Field(..., ge=1)

    @mcp.tool(name="db_delete_note", annotations={"readOnlyHint": False, "destructiveHint": True})
    async def db_delete_note(params: DeleteInput) -> str:
        """Delete a note from the agent database, confirming it is gone."""
        if agent_db.delete_note(_ROOT, params.note_id):
            return f"✓ Deleted note {params.note_id} (confirmed absent)."
        return f"No note with id {params.note_id} — nothing deleted."

    @mcp.tool(name="db_status", annotations={"readOnlyHint": True})
    async def db_status() -> str:
        """Where the agent database lives and how much is in it."""
        s = agent_db.stats(_ROOT)
        import datetime
        last = (datetime.datetime.fromtimestamp(s["last_write"]).strftime("%Y-%m-%d %H:%M")
                if s["last_write"] else "never")
        return (f"## Agent database\n\n- notes: {s['notes']}\n- last write: {last}\n"
                f"- file: `{s['path']}` ({s['size_bytes']} bytes)")
