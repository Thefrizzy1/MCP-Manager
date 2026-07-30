"""Nextcloud CalDAV: namespace-correct discovery and real-UTC time windows.

Two independent bugs made most of the Nextcloud calendar surface fail while the
server was perfectly healthy:

1. Discovery filtered on the literal prefix `<c:calendar>`. Nextcloud runs
   sabre/dav, which declares CalDAV as `cal:` — so the filter matched nothing,
   every calendar was discarded, and nextcloud_list_calendars reported "no
   calendars found" on an account that demonstrably had them. That cascaded:
   get_tasks/add_task fell back to a literal "tasks" slug that does not exist on a
   stock install, and the add-event round trip picked a guessed calendar.

2. Time-range queries formatted a Europe/Berlin wall time with a `Z` suffix, which
   asserts UTC. In summer that shifted the window two hours, so an event created
   five minutes ahead landed *before* the window start and could not be found —
   the add-event verify step failed on an event that had just been created.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from core.invoke_tool import invoke_mcp_tool_fn
from tools.nextcloud import caldav_utc, parse_dav_multistatus

# What Nextcloud actually sends: DAV as d:, CalDAV as cal:.
SABRE_BODY = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns"
               xmlns:cal="urn:ietf:params:xml:ns:caldav"
               xmlns:cs="http://calendarserver.org/ns/">
  <d:response>
    <d:href>/remote.php/dav/calendars/friso/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
    <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/friso/personal/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Personal</d:displayname>
      <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
      <cal:supported-calendar-component-set>
        <cal:comp name="VEVENT"/><cal:comp name="VTODO"/>
      </cal:supported-calendar-component-set>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/friso/meal%20plan/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Meal plan</d:displayname>
      <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
      <cal:supported-calendar-component-set><cal:comp name="VEVENT"/></cal:supported-calendar-component-set>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/friso/inbox/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Inbox</d:displayname>
      <d:resourcetype><d:collection/><cal:schedule-inbox/></d:resourcetype>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>"""


# ── discovery ────────────────────────────────────────────────────────────────

def test_calendars_are_found_with_the_cal_prefix():
    """The regression: a `cal:`-prefixed body used to yield nothing at all."""
    entries = parse_dav_multistatus(SABRE_BODY)
    cals = [e for e in entries if e["is_calendar"]]
    assert len(cals) == 2, "sabre/dav uses cal:, and both calendars must be seen"
    assert {e["displayname"] for e in cals} == {"Personal", "Meal plan"}


def test_component_support_is_read():
    personal = next(e for e in parse_dav_multistatus(SABRE_BODY)
                    if e["displayname"] == "Personal")
    assert set(personal["comps"]) == {"VEVENT", "VTODO"}
    meal = next(e for e in parse_dav_multistatus(SABRE_BODY)
                if e["displayname"] == "Meal plan")
    assert meal["comps"] == ["VEVENT"]


def test_non_calendar_collections_are_not_calendars():
    """schedule-inbox/outbox and the calendar home must not look like calendars."""
    entries = parse_dav_multistatus(SABRE_BODY)
    inbox = next(e for e in entries if e["displayname"] == "Inbox")
    assert inbox["is_calendar"] is False
    home = next(e for e in entries if e["href"].endswith("/friso/"))
    assert home["is_calendar"] is False


def test_a_c_prefixed_server_still_works():
    """Prefixes are the server's choice — matching on the URI has to accept any.
    (The old code only understood `c:`, which is why this direction never broke.)"""
    body = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response><d:href>/remote.php/dav/calendars/friso/personal/</d:href>
    <d:propstat><d:prop><d:displayname>Personal</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
      <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
    </d:prop></d:propstat></d:response>
  <d:response><d:href>/remote.php/dav/calendars/friso/work/</d:href>
    <d:propstat><d:prop><d:displayname>Work</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
    </d:prop></d:propstat></d:response>
</d:multistatus>"""
    cals = [e for e in parse_dav_multistatus(body) if e["is_calendar"]]
    assert len(cals) == 2
    assert {e["displayname"] for e in cals} == {"Personal", "Work"}


def test_uppercase_prefixes_work_too():
    body = SABRE_BODY.replace("<d:", "<D:").replace("</d:", "</D:").replace('xmlns:d=', 'xmlns:D=')
    cals = [e for e in parse_dav_multistatus(body) if e["is_calendar"]]
    assert len(cals) == 2


def test_malformed_xml_degrades_to_empty():
    assert parse_dav_multistatus("<not xml") == []
    assert parse_dav_multistatus("") == []


def test_file_listing_fields_survive():
    body = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/remote.php/dav/files/friso/Readme.md</d:href>
    <d:propstat><d:prop><d:displayname>Readme.md</d:displayname>
      <d:getcontentlength>197</d:getcontentlength>
      <d:resourcetype/></d:prop></d:propstat></d:response>
  <d:response><d:href>/remote.php/dav/files/friso/Notes/</d:href>
    <d:propstat><d:prop><d:displayname>Notes</d:displayname>
      <d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
</d:multistatus>"""
    entries = parse_dav_multistatus(body)
    f = next(e for e in entries if e["displayname"] == "Readme.md")
    assert f["contentlength"] == 197 and f["is_collection"] is False
    d = next(e for e in entries if e["displayname"] == "Notes")
    assert d["is_collection"] is True and d["contentlength"] is None


# ── the tools, wired up against a fake server ────────────────────────────────

def _nextcloud_tools(monkeypatch, handler):
    """Register the Nextcloud tools with HTTP replaced by `handler`."""
    import httpx
    from mcp.server.fastmcp import FastMCP

    import tools.nextcloud as NC
    from config import cfg

    monkeypatch.setattr(cfg, "nextcloud_url", "https://nc.example", raising=False)
    monkeypatch.setattr(cfg, "nextcloud_username", "friso", raising=False)
    monkeypatch.setattr(cfg, "nextcloud_password", "pw", raising=False)

    real = httpx.AsyncClient

    def fake(**kw):
        kw.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(NC.httpx, "AsyncClient", fake)

    m = FastMCP("t")
    NC.register_nextcloud_tools(m)
    return {t.name: t.fn for t in m._tool_manager.list_tools()}


def test_list_calendars_renders_the_real_calendars(monkeypatch):
    import asyncio

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=SABRE_BODY,
                              headers={"content-type": "application/xml"})

    fns = _nextcloud_tools(monkeypatch, handler)
    out = asyncio.run(fns["nextcloud_list_calendars"]())

    assert "No Nextcloud calendars found" not in out
    assert "`personal`" in out and "`meal plan`" in out
    assert "events + tasks" in out          # Personal advertises VEVENT + VTODO
    assert "Inbox" not in out               # schedule-inbox is not a calendar


def test_get_tasks_auto_resolves_a_vtodo_calendar(monkeypatch):
    """The old default asked for a literal "tasks" calendar, which stock Nextcloud
    does not have — every call 404'd."""
    import asyncio

    import httpx

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=SABRE_BODY,
                                  headers={"content-type": "application/xml"})
        asked.append(request.url.path)
        return httpx.Response(207, text="""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"/>""",
                              headers={"content-type": "application/xml"})

    fns = _nextcloud_tools(monkeypatch, handler)
    out = str(asyncio.run(invoke_mcp_tool_fn(fns["nextcloud_get_tasks"],
                                             payload={"list_name": "", "include_completed": False})))

    assert asked, "the REPORT was never issued"
    assert "/personal/" in asked[0], f"expected the VTODO calendar, got {asked[0]}"
    assert "tasks/" not in asked[0]
    assert "does not exist" not in out


def test_a_bad_slug_is_reported_with_the_real_ones(monkeypatch):
    import asyncio

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=SABRE_BODY,
                                  headers={"content-type": "application/xml"})
        return httpx.Response(207, text="""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:"/>""", headers={"content-type": "application/xml"})

    fns = _nextcloud_tools(monkeypatch, handler)
    out = str(asyncio.run(invoke_mcp_tool_fn(fns["nextcloud_get_tasks"],
                                             payload={"list_name": "nope", "include_completed": False})))

    # Falls back to a real calendar and says so, instead of telling the user to go
    # run another tool.
    assert "not a calendar on this server" in out
    assert "`personal`" in out


# ── time windows ─────────────────────────────────────────────────────────────

def test_caldav_stamp_is_real_utc_not_local_wall_time():
    """A Berlin summer time formatted with Z used to claim a time 2h off, which
    pushed a just-created event outside the queried window."""
    berlin = datetime(2026, 7, 30, 12, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert caldav_utc(berlin) == "20260730T100000Z"          # CEST is UTC+2


def test_caldav_stamp_handles_winter_offset():
    berlin = datetime(2026, 1, 15, 12, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert caldav_utc(berlin) == "20260115T110000Z"          # CET is UTC+1


def test_naive_datetimes_are_treated_as_berlin():
    naive = datetime(2026, 7, 30, 12, 0, 0)
    assert caldav_utc(naive) == "20260730T100000Z"


def test_a_freshly_created_event_falls_inside_the_window():
    """The exact failure shape: event five minutes out, one-day window."""
    from datetime import timedelta

    now = datetime.now(ZoneInfo("Europe/Berlin"))
    start = caldav_utc(now)
    end = caldav_utc(now + timedelta(days=1))
    event = caldav_utc(now + timedelta(minutes=5))
    assert start < event < end, (start, event, end)
