"""
tools/nextcloud.py — Complete Nextcloud integration
Covers: CalDAV (calendars, events, tasks), CardDAV (contacts), WebDAV (files), OCS (shares, activity, notes, user)
"""

import re
import uuid
import httpx
from typing import Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error, fmt_size


def register_nextcloud_tools(mcp: FastMCP):

    def _auth():
        return (cfg.nextcloud_username, cfg.nextcloud_password)

    def _nc(path: str) -> str:
        return f"{cfg.nextcloud_url}/{path.lstrip('/')}"

    def _quote_path(path: str) -> str:
        """Percent-encode each segment of a WebDAV/CalDAV path.

        Nextcloud rejects unencoded spaces and most non-ASCII characters in the
        URL, so 'Hausätredies/My Note.md' must become 'Haus%C3%A4tredies/My%20Note.md'.
        We split on '/' and encode each segment with safe='' so ':', '@', etc. also
        get encoded inside segments.
        """
        from urllib.parse import quote
        if not path:
            return ""
        return "/".join(quote(seg, safe="") for seg in path.split("/"))

    def _dav(path: str = "") -> str:
        return _nc(f"remote.php/dav/{_quote_path(path)}")

    def _webdav(path: str = "") -> str:
        return _nc(f"remote.php/webdav/{_quote_path(path)}")

    def _ocs(path: str) -> str:
        return _nc(f"ocs/v2.php/{path}")

    def _caldav(calendar: str = "") -> str:
        base = f"calendars/{cfg.nextcloud_username}/"
        return _dav(base + calendar)

    def _carddav(book: str = "") -> str:
        # Nextcloud canonical path: remote.php/dav/addressbooks/users/{userId}/{addressbook}/
        base = f"addressbooks/users/{cfg.nextcloud_username}/"
        return _dav(base + book)

    async def _discover_contacts_book_path() -> str:
        """Pick a working Contacts addressbook (slug varies by locale / NC version)."""
        candidates = ("contacts/", "Contacts/", "default/", "personal/", "Persönlich/")
        probe = b"""<?xml version="1.0"?><c:addressbook-query xmlns:c="urn:ietf:params:xml:ns:carddav" xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></c:addressbook-query>"""
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            for book in candidates:
                try:
                    r = await client.request(
                        "REPORT",
                        _carddav(book),
                        auth=_auth(),
                        headers={"Depth": "0", "Content-Type": "application/xml"},
                        content=probe,
                    )
                    if r.status_code < 400:
                        return book
                except Exception:
                    continue
        return "contacts/"

    def _now_berlin() -> datetime:
        return datetime.now(ZoneInfo("Europe/Berlin"))

    def _parse_ical_blocks(raw: str) -> list[dict]:
        """Parse iCal text into list of property dicts."""
        if not raw:
            return []
        unfolded = raw.replace("\r\n ", "").replace("\r\n", "\n").replace("\r", "\n")
        blocks = unfolded.split("BEGIN:VEVENT")[1:]
        results = []
        for block in blocks:
            def get(key):
                m = re.search(rf"^{key}[;:]([^\n]+)", block, re.MULTILINE)
                return m.group(1).strip() if m else ""
            results.append({
                "summary": get("SUMMARY"),
                "dtstart": get("DTSTART"),
                "dtend": get("DTEND"),
                "description": get("DESCRIPTION").replace("\\n", "\n"),
                "location": get("LOCATION"),
                "uid": get("UID"),
                "status": get("STATUS"),
                "rrule": get("RRULE"),
            })
        return results

    def _parse_vtodo_blocks(raw: str) -> list[dict]:
        """Parse iCal VTODO blocks."""
        if not raw:
            return []
        unfolded = raw.replace("\r\n ", "").replace("\r\n", "\n").replace("\r", "\n")
        blocks = unfolded.split("BEGIN:VTODO")[1:]
        results = []
        for block in blocks:
            def get(key):
                m = re.search(rf"^{key}[;:]([^\n]+)", block, re.MULTILINE)
                return m.group(1).strip() if m else ""
            results.append({
                "summary": get("SUMMARY"),
                "status": get("STATUS"),
                "due": get("DUE"),
                "priority": get("PRIORITY"),
                "description": get("DESCRIPTION").replace("\\n", "\n"),
                "uid": get("UID"),
                "percent": get("PERCENT-COMPLETE"),
            })
        return results

    def _parse_vcard_blocks(raw: str) -> list[dict]:
        """Parse vCard data into list of contact dicts."""
        if not raw:
            return []
        cards = re.split(r"BEGIN:VCARD", raw)[1:]
        results = []
        for card in cards:
            def get(key):
                m = re.search(rf"^{key}[;:]([^\r\n]+)", card, re.MULTILINE)
                return m.group(1).strip() if m else ""
            def get_all(key):
                return re.findall(rf"^{key}[;:][^\r\n]+", card, re.MULTILINE)

            name = get("FN") or get("N").replace(";", " ").strip()
            emails = [e.split(":")[-1] for e in get_all("EMAIL")]
            phones = [p.split(":")[-1] for p in get_all("TEL")]
            org = get("ORG").replace(";", " ").strip()
            uid = get("UID")
            results.append({"name": name, "emails": emails, "phones": phones, "org": org, "uid": uid})
        return results

    def _dtstart_to_date(dtstart: str) -> str:
        """Extract YYYY-MM-DD from DTSTART value."""
        val = dtstart.split(":")[-1] if ":" in dtstart else dtstart
        return f"{val[:4]}-{val[4:6]}-{val[6:8]}" if len(val) >= 8 else val

    def _is_today_or_future(dtstart: str, days_ahead: int) -> bool:
        try:
            date_str = _dtstart_to_date(dtstart)
            event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            today = _now_berlin().date()
            return today <= event_date <= (today + timedelta(days=days_ahead))
        except Exception:
            return False

    def _ical_escape(value: str) -> str:
        """Escape a value for iCal TEXT fields per RFC 5545 §3.3.11.

        Required for SUMMARY / DESCRIPTION / LOCATION etc. — without it a
        comma in the title splits the value, a semicolon ends the property,
        and newlines invalidate the file.
        """
        s = str(value or "")
        # Order matters: escape backslash first.
        s = s.replace("\\", "\\\\")
        s = s.replace(";", "\\;")
        s = s.replace(",", "\\,")
        s = s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
        return s

    def _vcard_escape(value: str) -> str:
        """Escape a value for vCard TEXT fields per RFC 6350 §3.4."""
        s = str(value or "")
        s = s.replace("\\", "\\\\")
        s = s.replace(";", "\\;")
        s = s.replace(",", "\\,")
        s = s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
        return s

    # ─── CALENDARS ────────────────────────────────────────────────────────────

    @mcp.tool(name="nextcloud_list_calendars", annotations={"readOnlyHint": True})
    async def nextcloud_list_calendars() -> str:
        """List all Nextcloud calendars with their slugs for use in other tools.

        Tags each calendar with the components it supports (events, tasks, both).
        Use the slug from this output as the `calendar` / `list_name` argument.
        """
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            body = b"""<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname/>
    <d:resourcetype/>
    <c:supported-calendar-component-set/>
  </d:prop>
</d:propfind>"""
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.request("PROPFIND", _caldav(), auth=_auth(),
                    headers={"Depth": "1", "Content-Type": "application/xml"}, content=body)
                r.raise_for_status()

            # Parse the multistatus response per <d:response> block so href/displayname
            # stay aligned even when the server omits properties for some collections.
            # Namespace prefixes vary across servers (d:, D:), so match both.
            ns_d = r"(?:d|D)"
            ns_c = r"(?:c|C)"
            # Strip the calendar-home prefix (e.g. /remote.php/dav/calendars/<user>/)
            # so we can detect the root collection vs calendar children robustly.
            home_path = _caldav("").rstrip("/")
            # Only need the path portion to compare with hrefs in the response.
            try:
                from urllib.parse import urlparse
                home_path_only = urlparse(home_path).path.rstrip("/")
            except Exception:
                home_path_only = home_path.rstrip("/")

            response_re = re.compile(
                rf"<{ns_d}:response\b[^>]*>(.*?)</{ns_d}:response>", re.DOTALL
            )
            href_re = re.compile(rf"<{ns_d}:href>([^<]+)</{ns_d}:href>")
            name_re = re.compile(rf"<{ns_d}:displayname>([^<]*)</{ns_d}:displayname>")
            comp_re = re.compile(rf'<{ns_c}:comp\b[^/>]*\bname="([^"]+)"')
            is_calendar_re = re.compile(rf"<{ns_c}:calendar\b")

            calendars: list[tuple[str, str, list[str]]] = []  # (slug, displayname, comps)
            for block in response_re.findall(r.text):
                href_m = href_re.search(block)
                if not href_m:
                    continue
                href_path = href_m.group(1).strip()
                # Skip the calendar home itself.
                if href_path.rstrip("/") == home_path_only:
                    continue
                # Only keep <c:calendar/> resourcetypes (skips notifications, schedule-inbox, etc.)
                if not is_calendar_re.search(block):
                    continue
                slug = href_path.rstrip("/").split("/")[-1]
                if not slug:
                    continue
                name_m = name_re.search(block)
                display = (name_m.group(1).strip() if name_m else "") or slug
                comps = [c.upper() for c in comp_re.findall(block)]
                calendars.append((slug, display, comps))

            if not calendars:
                return "No Nextcloud calendars found for this user."

            calendars.sort(key=lambda c: c[1].lower())
            result = "## Nextcloud Calendars\n\n"
            for slug, display, comps in calendars:
                if comps:
                    has_e = "VEVENT" in comps
                    has_t = "VTODO" in comps
                    if has_e and has_t:
                        kind = "events + tasks"
                    elif has_e:
                        kind = "events"
                    elif has_t:
                        kind = "tasks"
                    else:
                        kind = ", ".join(c.lower() for c in comps)
                else:
                    kind = "events (assumed)"
                result += f"- **{display}** — slug: `{slug}` — {kind}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud CalDAV")

    class NcEventsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        calendar: str = Field(..., description="Calendar slug e.g. 'personal', 'work-calendar', 'meal-plan'")
        days_ahead: int = Field(default=14, description="Days ahead to fetch", ge=1, le=365)

    @mcp.tool(name="nextcloud_get_events", annotations={"readOnlyHint": True})
    async def nextcloud_get_events(params: NcEventsInput) -> str:
        """Get upcoming events from a Nextcloud calendar.

        Use nextcloud_list_calendars to find calendar slugs.
        Automatically resolves 'today', 'tomorrow', 'this week' relative to Europe/Berlin timezone.
        """
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            now = _now_berlin()
            end = now + timedelta(days=params.days_ahead)
            start_str = now.strftime("%Y%m%dT%H%M%SZ")
            end_str = end.strftime("%Y%m%dT%H%M%SZ")

            body = f"""<?xml version="1.0"?>
<c:calendar-query xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start_str}" end="{end_str}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.request("REPORT", _caldav(params.calendar + "/"), auth=_auth(),
                    headers={"Depth": "1", "Content-Type": "application/xml"}, content=body.encode())
                r.raise_for_status()

            cal_data = re.findall(r"<cal:calendar-data[^>]*>(.*?)</cal:calendar-data>", r.text, re.DOTALL)
            if not cal_data:
                cal_data = re.findall(r"<c:calendar-data[^>]*>(.*?)</c:calendar-data>", r.text, re.DOTALL)

            all_events = []
            for data in cal_data:
                events = _parse_ical_blocks(data)
                all_events.extend(events)

            if not all_events:
                return f"No events in '{params.calendar}' for the next {params.days_ahead} days."

            result = f"## {params.calendar} — Next {params.days_ahead} days\n\n"
            for ev in sorted(all_events, key=lambda x: x.get("dtstart", "")):
                date = _dtstart_to_date(ev.get("dtstart", ""))
                time_part = ev.get("dtstart", "")
                if "T" in time_part:
                    raw_time = time_part.split("T")[-1].replace("Z", "")
                    hour = int(raw_time[:2])
                    minute = raw_time[2:4]
                    time_str = f" {hour:02d}:{minute}"
                else:
                    time_str = " (all day)"

                result += f"**{ev.get('summary', 'Untitled')}**\n"
                result += f"  📅 {date}{time_str}\n"
                if ev.get("location"):
                    result += f"  📍 {ev['location']}\n"
                if ev.get("description"):
                    desc = ev["description"][:100]
                    result += f"  📝 {desc}{'...' if len(ev['description']) > 100 else ''}\n"
                if ev.get("uid"):
                    result += f"  UID: `{ev['uid']}`\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud CalDAV")

    class NcAddEventInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        calendar: str = Field(..., description="Calendar slug e.g. 'personal'")
        title: str = Field(..., description="Event title", min_length=1, max_length=500)
        date: str = Field(..., description="Date in YYYY-MM-DD format e.g. '2026-05-10'")
        start_time: Optional[str] = Field(default=None, description="Start time HH:MM e.g. '14:30'. Omit for all-day event.")
        end_time: Optional[str] = Field(default=None, description="End time HH:MM. Defaults to 1 hour after start.")
        description: Optional[str] = Field(default=None, description="Event description/notes")
        location: Optional[str] = Field(default=None, description="Event location")

    @mcp.tool(name="nextcloud_add_event", annotations={"readOnlyHint": False})
    async def nextcloud_add_event(params: NcAddEventInput) -> str:
        """Add a new event to a Nextcloud calendar.

        For time-relative dates, first call get_context to know today's date.
        Example: date='2026-05-10', start_time='14:00', end_time='15:00'
        """
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            uid = str(uuid.uuid4())
            now_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            date_clean = params.date.replace("-", "")

            if params.start_time:
                start_clean = params.start_time.replace(":", "")
                if params.end_time:
                    end_clean = params.end_time.replace(":", "")
                else:
                    # Default 1 hour duration
                    h, m = int(start_clean[:2]), int(start_clean[2:])
                    h = (h + 1) % 24
                    end_clean = f"{h:02d}{m:02d}"
                dtstart = f"DTSTART;TZID=Europe/Berlin:{date_clean}T{start_clean}00"
                dtend = f"DTEND;TZID=Europe/Berlin:{date_clean}T{end_clean}00"
            else:
                dtstart = f"DTSTART;VALUE=DATE:{date_clean}"
                next_day = (datetime.strptime(params.date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
                dtend = f"DTEND;VALUE=DATE:{next_day}"

            lines = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Plutus MCP//EN",
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_str}",
                dtstart,
                dtend,
                f"SUMMARY:{_ical_escape(params.title)}",
            ]
            if params.description:
                lines.append(f"DESCRIPTION:{_ical_escape(params.description)}")
            if params.location:
                lines.append(f"LOCATION:{_ical_escape(params.location)}")
            lines += ["END:VEVENT", "END:VCALENDAR"]
            ical = "\r\n".join(lines)

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.put(
                    _caldav(f"{params.calendar}/{uid}.ics"), auth=_auth(),
                    headers={"Content-Type": "text/calendar; charset=utf-8"},
                    content=ical.encode()
                )
                r.raise_for_status()

            time_str = f" at {params.start_time}" if params.start_time else " (all day)"
            return f"✓ Event '{params.title}' added to {params.calendar} on {params.date}{time_str}\nUID: `{uid}`"
        except Exception as e:
            return _handle_error(e, "Nextcloud CalDAV")

    class NcDeleteEventInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        calendar: str = Field(..., description="Calendar slug")
        uid: str = Field(..., description="Event UID from nextcloud_get_events", min_length=1)

    @mcp.tool(name="nextcloud_delete_event", annotations={"readOnlyHint": False, "destructiveHint": True})
    async def nextcloud_delete_event(params: NcDeleteEventInput) -> str:
        """Delete a calendar event by its UID."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.delete(
                    _caldav(f"{params.calendar}/{params.uid}.ics"), auth=_auth()
                )
                r.raise_for_status()
            return f"✓ Event deleted: `{params.uid}`"
        except Exception as e:
            return _handle_error(e, "Nextcloud CalDAV")

    # ─── TASKS (VTODO) ────────────────────────────────────────────────────────

    class NcTasksInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        list_name: str = Field(default="tasks", description="Task list calendar slug")
        include_completed: bool = Field(default=False, description="Include completed tasks")

    @mcp.tool(name="nextcloud_get_tasks", annotations={"readOnlyHint": True})
    async def nextcloud_get_tasks(params: NcTasksInput) -> str:
        """Get tasks from Nextcloud Tasks app via CalDAV (VTODO).

        `list_name` must match a calendar slug from nextcloud_list_calendars.
        Calendars are tagged with which components they support (events / tasks).
        """
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            body = b"""<?xml version="1.0"?><c:calendar-query xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:"><d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VTODO"/></c:comp-filter></c:filter></c:calendar-query>"""
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.request("REPORT", _caldav(params.list_name + "/"), auth=_auth(),
                    headers={"Depth": "1", "Content-Type": "application/xml"}, content=body)
                if r.status_code == 404:
                    return (
                        f"Error: Calendar slug '{params.list_name}' does not exist. "
                        "Run `nextcloud_list_calendars` to see your real slugs and pick one tagged 'tasks' or 'events + tasks'."
                    )
                r.raise_for_status()

            cal_data = re.findall(r"<cal:calendar-data[^>]*>(.*?)</cal:calendar-data>", r.text, re.DOTALL)
            if not cal_data:
                cal_data = re.findall(r"<c:calendar-data[^>]*>(.*?)</c:calendar-data>", r.text, re.DOTALL)

            all_tasks = []
            for data in cal_data:
                all_tasks.extend(_parse_vtodo_blocks(data))

            if not params.include_completed:
                all_tasks = [t for t in all_tasks if t.get("status", "").upper() != "COMPLETED"]

            if not all_tasks:
                return f"No tasks found in '{params.list_name}'."

            result = f"## Nextcloud Tasks: {params.list_name} ({len(all_tasks)} tasks)\n\n"
            for task in all_tasks:
                done = "☑" if task.get("status", "").upper() == "COMPLETED" else "☐"
                result += f"{done} **{task.get('summary', 'Untitled')}**\n"
                if task.get("due"):
                    result += f"  Due: {_dtstart_to_date(task['due'])}\n"
                if task.get("priority"):
                    prio_map = {"1": "high", "5": "medium", "9": "low"}
                    result += f"  Priority: {prio_map.get(task['priority'], task['priority'])}\n"
                if task.get("description"):
                    result += f"  {task['description'][:100]}\n"
                if task.get("uid"):
                    result += f"  UID: `{task['uid']}`\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud Tasks")

    class NcAddTaskInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        title: str = Field(..., description="Task title", min_length=1, max_length=500)
        list_name: str = Field(default="tasks", description="Task list calendar slug")
        due_date: Optional[str] = Field(default=None, description="Due date YYYY-MM-DD")
        priority: str = Field(default="normal", description="Priority: 'high', 'normal', 'low'")
        description: Optional[str] = Field(default=None, description="Task notes")

    @mcp.tool(name="nextcloud_add_task", annotations={"readOnlyHint": False})
    async def nextcloud_add_task(params: NcAddTaskInput) -> str:
        """Add a new task to Nextcloud Tasks."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            uid = str(uuid.uuid4())
            now_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            prio_map = {"high": "1", "normal": "5", "low": "9"}
            priority = prio_map.get(params.priority, "5")

            lines = [
                "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Plutus MCP//EN",
                "BEGIN:VTODO",
                f"UID:{uid}", f"DTSTAMP:{now_str}",
                f"SUMMARY:{_ical_escape(params.title)}",
                f"PRIORITY:{priority}", "STATUS:NEEDS-ACTION",
            ]
            if params.due_date:
                lines.append(f"DUE;VALUE=DATE:{params.due_date.replace('-', '')}")
            if params.description:
                lines.append(f"DESCRIPTION:{_ical_escape(params.description)}")
            lines += ["END:VTODO", "END:VCALENDAR"]
            ical = "\r\n".join(lines)

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.put(
                    _caldav(f"{params.list_name}/{uid}.ics"), auth=_auth(),
                    headers={"Content-Type": "text/calendar; charset=utf-8"},
                    content=ical.encode()
                )
                r.raise_for_status()
            return f"✓ Task '{params.title}' added to {params.list_name}. UID: `{uid}`"
        except Exception as e:
            return _handle_error(e, "Nextcloud Tasks")

    class NcCompleteTaskInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        list_name: str = Field(..., description="Task list calendar slug")
        uid: str = Field(..., description="Task UID from nextcloud_get_tasks", min_length=1)

    @mcp.tool(name="nextcloud_complete_task", annotations={"readOnlyHint": False})
    async def nextcloud_complete_task(params: NcCompleteTaskInput) -> str:
        """Mark a Nextcloud task as completed."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            now_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                # Fetch existing task
                r = await client.get(_caldav(f"{params.list_name}/{params.uid}.ics"), auth=_auth())
                if r.status_code == 404:
                    return f"Error: Task not found: `{params.uid}`"
                r.raise_for_status()
                ical = r.text

            # Update status
            ical = re.sub(r"STATUS:[^\r\n]+", "STATUS:COMPLETED", ical)
            ical = re.sub(r"PERCENT-COMPLETE:[^\r\n]+", "PERCENT-COMPLETE:100", ical)
            if "PERCENT-COMPLETE" not in ical:
                ical = ical.replace("STATUS:COMPLETED", f"STATUS:COMPLETED\r\nPERCENT-COMPLETE:100")
            if "COMPLETED:" not in ical:
                ical = ical.replace("STATUS:COMPLETED", f"STATUS:COMPLETED\r\nCOMPLETED:{now_str}")

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.put(
                    _caldav(f"{params.list_name}/{params.uid}.ics"), auth=_auth(),
                    headers={"Content-Type": "text/calendar; charset=utf-8"},
                    content=ical.encode()
                )
                r.raise_for_status()
            return f"✓ Task completed: `{params.uid}`"
        except Exception as e:
            return _handle_error(e, "Nextcloud Tasks")

    class NcDeleteTaskInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        list_name: str = Field(..., description="Task list calendar slug")
        uid: str = Field(..., description="Task UID from nextcloud_get_tasks", min_length=1)

    @mcp.tool(name="nextcloud_delete_task", annotations={"readOnlyHint": False, "destructiveHint": True})
    async def nextcloud_delete_task(params: NcDeleteTaskInput) -> str:
        """Delete a Nextcloud task by its UID."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.delete(_caldav(f"{params.list_name}/{params.uid}.ics"), auth=_auth())
                r.raise_for_status()
            return f"✓ Task deleted: `{params.uid}`"
        except Exception as e:
            return _handle_error(e, "Nextcloud Tasks")

    # ─── CONTACTS (CARDDAV) ───────────────────────────────────────────────────

    @mcp.tool(name="nextcloud_list_contacts", annotations={"readOnlyHint": True})
    async def nextcloud_list_contacts() -> str:
        """List all contacts from Nextcloud Contacts (CardDAV)."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            book = await _discover_contacts_book_path()
            body = b"""<?xml version="1.0"?><c:addressbook-query xmlns:c="urn:ietf:params:xml:ns:carddav" xmlns:d="DAV:"><d:prop><d:getetag/><c:address-data/></d:prop></c:addressbook-query>"""
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                r = await client.request(
                    "REPORT",
                    _carddav(book),
                    auth=_auth(),
                    headers={"Depth": "1", "Content-Type": "application/xml"},
                    content=body,
                )
                r.raise_for_status()

            vcards = re.findall(r"<card:address-data[^>]*>(.*?)</card:address-data>", r.text, re.DOTALL)
            if not vcards:
                vcards = re.findall(r"<c:address-data[^>]*>(.*?)</c:address-data>", r.text, re.DOTALL)

            contacts = []
            for vc in vcards:
                contacts.extend(_parse_vcard_blocks(vc))

            if not contacts:
                return "No contacts found."

            result = f"## Nextcloud Contacts ({len(contacts)} total)\n\n"
            for c in sorted(contacts, key=lambda x: x.get("name", "").lower()):
                result += f"**{c.get('name', 'Unknown')}**"
                if c.get("org"):
                    result += f" — {c['org']}"
                result += "\n"
                for email in c.get("emails", []):
                    result += f"  📧 {email}\n"
                for phone in c.get("phones", []):
                    result += f"  📞 {phone}\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud CardDAV")

    class NcSearchContactsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search query — name, email, phone, or organisation", min_length=1, max_length=200)

    @mcp.tool(name="nextcloud_search_contacts", annotations={"readOnlyHint": True})
    async def nextcloud_search_contacts(params: NcSearchContactsInput) -> str:
        """Search Nextcloud contacts by name, email, phone, or organisation."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            # Use OCS search API
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    _ocs("apps/contacts/api/v1/search"), auth=_auth(),
                    headers={"OCS-APIREQUEST": "true", "Accept": "application/json"},
                    params={"term": params.query, "limit": 20}
                )
                if r.status_code == 200:
                    data = r.json()
                    contacts_data = data.get("ocs", {}).get("data", [])
                    if contacts_data:
                        result = f"## Contact Search: '{params.query}'\n\n"
                        for c in contacts_data:
                            result += f"**{c.get('fullName', 'Unknown')}**\n"
                            for email in c.get("emailAddresses", []):
                                result += f"  📧 {email.get('value', '')}\n"
                            for phone in c.get("phoneNumbers", []):
                                result += f"  📞 {phone.get('value', '')}\n"
                            result += "\n"
                        return result

            # Fallback: fetch all and filter
            all_contacts = await nextcloud_list_contacts()
            query = params.query.lower()
            lines = all_contacts.split("\n")
            result = f"## Contact Search: '{params.query}'\n\n"
            matches = []
            current = []
            for line in lines:
                if line.startswith("**"):
                    if current and any(query in l.lower() for l in current):
                        matches.extend(current)
                        matches.append("")
                    current = [line]
                elif current:
                    current.append(line)
            if not matches:
                return f"No contacts matching '{params.query}'"
            result += "\n".join(matches)
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud Contacts")

    class NcAddContactInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        name: str = Field(..., description="Full name", min_length=1, max_length=200)
        email: Optional[str] = Field(default=None, description="Email address")
        phone: Optional[str] = Field(default=None, description="Phone number")
        organisation: Optional[str] = Field(default=None, description="Company/organisation name")
        notes: Optional[str] = Field(default=None, description="Additional notes")

    @mcp.tool(name="nextcloud_add_contact", annotations={"readOnlyHint": False})
    async def nextcloud_add_contact(params: NcAddContactInput) -> str:
        """Add a new contact to Nextcloud Contacts."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            uid = str(uuid.uuid4())
            now_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

            # Split name into parts
            name_parts = params.name.strip().split(" ", 1)
            first = name_parts[0]
            last = name_parts[1] if len(name_parts) > 1 else ""

            lines = [
                "BEGIN:VCARD", "VERSION:3.0",
                f"UID:{uid}",
                f"FN:{_vcard_escape(params.name)}",
                # N is special: components are separated by ';' so we escape ; and , but
                # do NOT escape the semicolon delimiters between fields.
                f"N:{_vcard_escape(last)};{_vcard_escape(first)};;;",
                f"REV:{now_str}",
            ]
            if params.email:
                lines.append(f"EMAIL;TYPE=INTERNET:{_vcard_escape(params.email)}")
            if params.phone:
                lines.append(f"TEL;TYPE=CELL:{_vcard_escape(params.phone)}")
            if params.organisation:
                lines.append(f"ORG:{_vcard_escape(params.organisation)}")
            if params.notes:
                lines.append(f"NOTE:{_vcard_escape(params.notes)}")
            lines.append("END:VCARD")
            vcard = "\r\n".join(lines)

            # _discover_contacts_book_path returns "contacts/" with trailing slash;
            # use rstrip only so we don't accidentally strip a leading one.
            book = (await _discover_contacts_book_path()).rstrip("/")
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                r = await client.put(
                    _carddav(f"{book}/{uid}.vcf"), auth=_auth(),
                    headers={"Content-Type": "text/vcard; charset=utf-8"},
                    content=vcard.encode()
                )
                r.raise_for_status()
            return f"✓ Contact '{params.name}' added. UID: `{uid}`"
        except Exception as e:
            return _handle_error(e, "Nextcloud Contacts")

    # ─── FILES (WEBDAV) ───────────────────────────────────────────────────────

    class NcFilesInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(default="", description="Path in Nextcloud files e.g. 'Documents/Projects'. Leave empty for root.")

    @mcp.tool(name="nextcloud_list_files", annotations={"readOnlyHint": True})
    async def nextcloud_list_files(params: NcFilesInput) -> str:
        """List files and folders in Nextcloud via WebDAV."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            body = b"""<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:getcontentlength/><d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>"""
            path = params.path.strip("/")
            url = _webdav(path + "/" if path else "")
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.request("PROPFIND", url, auth=_auth(),
                    headers={"Depth": "1", "Content-Type": "application/xml"}, content=body)
                r.raise_for_status()

            # Parse per <d:response> block so name + size + resourcetype stay
            # aligned for each entry. Two parallel re.findall sweeps drift apart
            # because directories don't emit <d:getcontentlength>.
            ns_d = r"(?:d|D)"
            response_re = re.compile(
                rf"<{ns_d}:response\b[^>]*>(.*?)</{ns_d}:response>", re.DOTALL
            )
            href_re = re.compile(rf"<{ns_d}:href>([^<]+)</{ns_d}:href>")
            name_re = re.compile(rf"<{ns_d}:displayname>([^<]*)</{ns_d}:displayname>")
            size_re = re.compile(
                rf"<{ns_d}:getcontentlength>([^<]*)</{ns_d}:getcontentlength>"
            )
            is_collection_re = re.compile(rf"<{ns_d}:collection\b")

            from urllib.parse import unquote

            entries: list[tuple[str, int | None, bool]] = []  # (name, size, is_dir)
            seen_self = False
            for block in response_re.findall(r.text):
                href_m = href_re.search(block)
                if not href_m:
                    continue
                # First response in a Depth:1 PROPFIND is the parent itself — skip.
                if not seen_self:
                    seen_self = True
                    continue
                name_m = name_re.search(block)
                if name_m and name_m.group(1).strip():
                    name = name_m.group(1).strip()
                else:
                    # Fall back to last href segment (some servers omit displayname).
                    name = unquote(href_m.group(1).rstrip("/").rsplit("/", 1)[-1])
                is_dir = bool(is_collection_re.search(block))
                size: int | None = None
                if not is_dir:
                    size_m = size_re.search(block)
                    if size_m and size_m.group(1).strip():
                        try:
                            size = int(size_m.group(1).strip())
                        except ValueError:
                            size = None
                entries.append((name, size, is_dir))

            dirs = sorted([(n, sz) for n, sz, d in entries if d], key=lambda x: x[0].lower())
            files = sorted([(n, sz) for n, sz, d in entries if not d], key=lambda x: x[0].lower())

            result = f"## Nextcloud Files: /{params.path or ''}\n\n"
            for name, _sz in dirs:
                result += f"📁 {name}/\n"
            for name, size in files:
                size_str = fmt_size(size) if isinstance(size, int) else "?"
                result += f"📄 {name} ({size_str})\n"
            result += f"\n{len(dirs)} folders, {len(files)} files"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud WebDAV")

    class NcReadFileInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="File path in Nextcloud e.g. 'Notes/todo.md'", min_length=1)

    @mcp.tool(name="nextcloud_read_file", annotations={"readOnlyHint": True})
    async def nextcloud_read_file(params: NcReadFileInput) -> str:
        """Read a text file from Nextcloud."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(_webdav(params.path), auth=_auth())
                if r.status_code == 404:
                    return f"Error: File not found: '{params.path}'"
                r.raise_for_status()
                content = r.text
            return f"## {params.path}\n\n{content[:5000]}{'...(truncated)' if len(content) > 5000 else ''}"
        except Exception as e:
            return _handle_error(e, "Nextcloud WebDAV")

    class NcUploadFileInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="Destination path in Nextcloud e.g. 'Notes/new-note.md'", min_length=1)
        content: str = Field(..., description="File content to upload")

    @mcp.tool(name="nextcloud_upload_file", annotations={"readOnlyHint": False})
    async def nextcloud_upload_file(params: NcUploadFileInput) -> str:
        """Upload/create a file in Nextcloud."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.put(_webdav(params.path), auth=_auth(),
                    content=params.content.encode())
                r.raise_for_status()
            return f"✓ File uploaded: '{params.path}' ({len(params.content)} chars)"
        except Exception as e:
            return _handle_error(e, "Nextcloud WebDAV")

    class NcDeleteFileInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="File or folder path to delete", min_length=1)

    @mcp.tool(name="nextcloud_delete_file", annotations={"readOnlyHint": False, "destructiveHint": True})
    async def nextcloud_delete_file(params: NcDeleteFileInput) -> str:
        """Delete a file or folder from Nextcloud."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.delete(_webdav(params.path), auth=_auth())
                r.raise_for_status()
            return f"✓ Deleted: '{params.path}'"
        except Exception as e:
            return _handle_error(e, "Nextcloud WebDAV")

    class NcMoveFileInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        source: str = Field(..., description="Source path in Nextcloud", min_length=1)
        destination: str = Field(..., description="Destination path in Nextcloud", min_length=1)

    @mcp.tool(name="nextcloud_move_file", annotations={"readOnlyHint": False})
    async def nextcloud_move_file(params: NcMoveFileInput) -> str:
        """Move or rename a file in Nextcloud."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            dest_url = _webdav(params.destination)
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.request("MOVE", _webdav(params.source), auth=_auth(),
                    headers={"Destination": dest_url, "Overwrite": "T"})
                r.raise_for_status()
            return f"✓ Moved '{params.source}' → '{params.destination}'"
        except Exception as e:
            return _handle_error(e, "Nextcloud WebDAV")

    class NcShareInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="File or folder path to share", min_length=1)
        share_type: int = Field(default=3, description="Share type: 3=public link, 0=user, 4=email")
        permissions: int = Field(default=1, description="Permissions: 1=read, 17=read+share, 31=all")
        password: Optional[str] = Field(default=None, description="Optional password for public link")
        expiry: Optional[str] = Field(default=None, description="Expiry date YYYY-MM-DD")

    @mcp.tool(name="nextcloud_share_file", annotations={"readOnlyHint": False})
    async def nextcloud_share_file(params: NcShareInput) -> str:
        """Create a share link for a Nextcloud file or folder."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            body = {
                "path": "/" + params.path.lstrip("/"),
                "shareType": params.share_type,
                "permissions": params.permissions,
            }
            if params.password:
                body["password"] = params.password
            if params.expiry:
                body["expireDate"] = params.expiry

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    _ocs("apps/files_sharing/api/v1/shares"), auth=_auth(),
                    headers={"OCS-APIREQUEST": "true", "Content-Type": "application/json",
                             "Accept": "application/json"},
                    json=body
                )
                r.raise_for_status()
                data = r.json()

            share_data = data.get("ocs", {}).get("data", {})
            url = share_data.get("url", "")
            share_id = share_data.get("id", "")
            return f"✓ Share created!\nURL: {url}\nShare ID: {share_id}"
        except Exception as e:
            return _handle_error(e, "Nextcloud Sharing")

    # ─── OCS / USER / ACTIVITY ────────────────────────────────────────────────

    @mcp.tool(name="nextcloud_get_user_info", annotations={"readOnlyHint": True})
    async def nextcloud_get_user_info() -> str:
        """Get Nextcloud user info — quota, display name, email, groups."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    _ocs(f"cloud/users/{cfg.nextcloud_username}"), auth=_auth(),
                    headers={"OCS-APIREQUEST": "true", "Accept": "application/json"}
                )
                r.raise_for_status()
                data = r.json().get("ocs", {}).get("data", {})

            result = "## Nextcloud User Info\n\n"
            result += f"**Display Name:** {data.get('displayname', '?')}\n"
            result += f"**Email:** {data.get('email', '?')}\n"
            result += f"**Groups:** {', '.join(data.get('groups', []))}\n\n"

            quota = data.get("quota", {})
            if quota:
                used = quota.get("used", 0)
                total = quota.get("quota", 0)
                free = quota.get("free", 0)
                result += f"**Storage:** {fmt_size(used)} used"
                if total > 0:
                    result += f" / {fmt_size(total)} total ({fmt_size(free)} free)"
                result += "\n"

            result += f"**Nextcloud version:** {data.get('backend', '?')}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud OCS")

    @mcp.tool(name="nextcloud_get_activity", annotations={"readOnlyHint": True})
    async def nextcloud_get_activity() -> str:
        """Get recent Nextcloud activity feed — file changes, shares, comments."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    _ocs("apps/activity/api/v2/activity"), auth=_auth(),
                    headers={"OCS-APIREQUEST": "true", "Accept": "application/json"},
                    params={"limit": 20, "object_type": "", "object_id": 0, "sort": "desc"}
                )
                r.raise_for_status()
                activities = r.json().get("ocs", {}).get("data", [])

            if not activities:
                return "No recent activity found."

            result = f"## Nextcloud Activity (last {len(activities)} events)\n\n"
            for act in activities:
                # `datetime` may be missing or None on some legacy events;
                # guard against `None[:16]` crashing the entire feed.
                ts_raw = act.get("datetime") or ""
                timestamp = str(ts_raw)[:16].replace("T", " ") if ts_raw else "(no timestamp)"
                subject = act.get("subject", "")
                app = act.get("app", "")
                result += f"**{timestamp}** [{app}] {subject}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud Activity")

    @mcp.tool(name="nextcloud_get_notifications", annotations={"readOnlyHint": True})
    async def nextcloud_get_notifications() -> str:
        """Get unread Nextcloud notifications."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    _ocs("apps/admin_notifications/api/v1/notifications/userNotifications"), auth=_auth(),
                    headers={"OCS-APIREQUEST": "true", "Accept": "application/json"}
                )
                if r.status_code == 404:
                    # Try alternative endpoint
                    r = await client.get(
                        _ocs("apps/notifications/api/v2/notifications"), auth=_auth(),
                        headers={"OCS-APIREQUEST": "true", "Accept": "application/json"}
                    )
                r.raise_for_status()
                notifications = r.json().get("ocs", {}).get("data", [])

            if not notifications:
                return "No unread notifications."

            result = f"## Nextcloud Notifications ({len(notifications)})\n\n"
            for n in notifications:
                subject = n.get("subject", "")
                message = n.get("message", "")
                app = n.get("app", "")
                ts_raw = n.get("datetime") or ""
                timestamp = str(ts_raw)[:16].replace("T", " ") if ts_raw else ""
                result += f"**[{app}]** {subject}\n"
                if message:
                    result += f"  {message}\n"
                if timestamp:
                    result += f"  {timestamp}\n\n"
                else:
                    result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud Notifications")

    class NcNotesInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        limit: int = Field(default=20, description="Max notes to return", ge=1, le=100)

    @mcp.tool(name="nextcloud_get_notes", annotations={"readOnlyHint": True})
    async def nextcloud_get_notes(params: NcNotesInput) -> str:
        """Get notes from Nextcloud Notes app."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    _nc("index.php/apps/notes/api/v1/notes"), auth=_auth(),
                    headers={"Accept": "application/json"},
                    params={"pruneBefore": 0}
                )
                r.raise_for_status()
                notes = r.json()

            if not notes:
                return "No notes found."

            result = f"## Nextcloud Notes ({len(notes)} total)\n\n"
            for note in notes[:params.limit]:
                title = note.get("title", "Untitled")
                modified = note.get("modified", 0)
                category = note.get("category", "")
                favorite = "⭐ " if note.get("favorite") else ""
                note_id = note.get("id")
                result += f"{favorite}**{title}**"
                if category:
                    result += f" [{category}]"
                result += f" (ID: {note_id})\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud Notes")

    class NcGetNoteInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        note_id: int = Field(..., description="Note ID from nextcloud_get_notes", ge=1)

    @mcp.tool(name="nextcloud_read_note", annotations={"readOnlyHint": True})
    async def nextcloud_read_note(params: NcGetNoteInput) -> str:
        """Read the full content of a Nextcloud note by ID."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    _nc(f"index.php/apps/notes/api/v1/notes/{params.note_id}"), auth=_auth(),
                    headers={"Accept": "application/json"}
                )
                r.raise_for_status()
                note = r.json()

            title = note.get("title", "Untitled")
            content = note.get("content", "")
            category = note.get("category", "")
            return f"## Note: {title}\nCategory: {category or 'None'}\n\n{content}"
        except Exception as e:
            return _handle_error(e, "Nextcloud Notes")

    class NcCreateNoteInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        title: str = Field(..., description="Note title", min_length=1, max_length=200)
        content: str = Field(..., description="Note content in Markdown")
        category: str = Field(default="", description="Category/folder name")

    @mcp.tool(name="nextcloud_create_note", annotations={"readOnlyHint": False})
    async def nextcloud_create_note(params: NcCreateNoteInput) -> str:
        """Create a new note in Nextcloud Notes."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            body = {"title": params.title, "content": params.content, "category": params.category}
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    _nc("index.php/apps/notes/api/v1/notes"), auth=_auth(),
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json=body
                )
                r.raise_for_status()
                note = r.json()
            return f"✓ Note created: '{params.title}' (ID: {note.get('id')})"
        except Exception as e:
            return _handle_error(e, "Nextcloud Notes")

    @mcp.tool(name="nextcloud_list_shares", annotations={"readOnlyHint": True})
    async def nextcloud_list_shares() -> str:
        """List all active file shares in Nextcloud."""
        if not cfg.is_configured("nextcloud_url", "nextcloud_username", "nextcloud_password"):
            return "Error: Nextcloud not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                # Both the Accept header and the explicit ?format=json are
                # needed — older Nextcloud installs honour only the query
                # param and otherwise return XML, which crashes r.json().
                r = await client.get(
                    _ocs("apps/files_sharing/api/v1/shares"), auth=_auth(),
                    headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                    params={"format": "json"},
                )
                r.raise_for_status()
                shares = r.json().get("ocs", {}).get("data", [])

            if not shares:
                return "No active shares."

            type_map = {0: "User", 1: "Group", 3: "Public link", 4: "Email", 6: "Federated"}
            result = f"## Nextcloud Shares ({len(shares)})\n\n"
            for share in shares:
                stype = type_map.get(share.get("share_type", -1), "Unknown")
                path = share.get("path", "?")
                url = share.get("url", "")
                expiry = share.get("expiration", "")
                result += f"**{path}** — {stype}\n"
                if url:
                    result += f"  🔗 {url}\n"
                if expiry:
                    result += f"  Expires: {expiry[:10]}\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Nextcloud Shares")
