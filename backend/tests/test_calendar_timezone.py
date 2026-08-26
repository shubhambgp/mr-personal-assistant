"""The zone a meeting is booked in — and why UTC must not be a guess.

The bug: every timezone endpoint on the Calendar API needs a calendar *settings*
scope, and this app requests `calendar.events`. So `gcal.timezone()` 403'd on
every connection, the callback stored the string "UTC" as though Google had said
it, and a rep who asked for an 11:00 meeting got 11:00 UTC — 16:30 in IST. The
fix is that "Google would not say" is now NULL, distinct from "the calendar is
in UTC", and one resolver applies the fallback.
"""

from __future__ import annotations

import inspect
import json

import pytest

from app.bot.context import RepContext
from app.config import settings
from app.services import agenda as agenda_service

pytestmark = pytest.mark.asyncio

CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="T", email_account="r@example.test")


def _conn(tz):
    return agenda_service.Connection(
        chair_id=CTX.chair_id, rep_code=CTX.rep_code, email_account="r@example.test",
        scopes=("https://www.googleapis.com/auth/calendar.events",), calendar_tz=tz,
    )


async def test_unknown_zone_falls_back_to_the_configured_one(monkeypatch):
    monkeypatch.setattr(settings, "agenda_timezone", "Asia/Kolkata")
    assert _conn(None).effective_tz == "Asia/Kolkata"


async def test_a_zone_google_actually_reported_wins(monkeypatch):
    monkeypatch.setattr(settings, "agenda_timezone", "Asia/Kolkata")
    assert _conn("Europe/London").effective_tz == "Europe/London"


async def test_utc_is_only_ever_used_when_the_server_says_so(monkeypatch):
    """The distinction the old code destroyed: an unknown zone must not become
    UTC unless UTC is genuinely the deployment's zone."""
    monkeypatch.setattr(settings, "agenda_timezone", "Asia/Kolkata")
    assert _conn(None).effective_tz != "UTC"
    monkeypatch.setattr(settings, "agenda_timezone", "UTC")
    assert _conn(None).effective_tz == "UTC"


async def test_an_11am_request_is_booked_at_11am_local(monkeypatch):
    """End to end through the service, with the Calendar API stubbed.

    This is the failure the rep reported: the hour they said, in the zone they
    meant, is what reaches Google.
    """
    monkeypatch.setattr(settings, "agenda_timezone", "Asia/Kolkata")
    sent: dict = {}

    async def token(_ctx):
        return "tok", _conn(None)          # the real state: Google never said

    async def create(_tok, **kw):
        sent.update(kw)
        return {"id": "ev-1"}

    async def no_events(_ctx, **kw):
        return []

    monkeypatch.setattr(agenda_service, "_access_token", token)
    monkeypatch.setattr(agenda_service.calendar, "create_event", create)
    monkeypatch.setattr(agenda_service, "events", no_events)
    monkeypatch.setattr(agenda_service, "record_outbound", lambda *a, **k: None)

    out = json.loads(await agenda_service.create_calendar_event(
        CTX, title="Private slot", starts_at="2026-09-01T11:00",
        duration_minutes=60, attendees=[], notes="", notify=False))

    assert sent["tz"] == "Asia/Kolkata"
    assert sent["starts_at"].isoformat() == "2026-09-01T11:00:00+05:30"
    assert out["starts_at"] == "2026-09-01T11:00:00+05:30"


async def test_no_booking_path_reads_the_raw_field():
    """The MECHANISM, in the style of test_logging_extras.py.

    The bug was not the fallback value, it was a caller reading `.calendar_tz`
    directly. Every place that books, moves or lists an event must go through
    `effective_tz`; the raw field is for storage and for Settings' own display.
    """
    source = inspect.getsource(agenda_service)
    # Trim the dataclass, which necessarily names the field it defines.
    body = source[source.index("def mark_stale("):]
    offenders = [
        line.strip()
        for line in body.splitlines()
        if "conn_row.calendar_tz" in line or "row.calendar_tz" in line
        if "effective_tz" not in line and not line.strip().startswith("#")
    ]
    assert offenders == [], f"these read the raw zone instead of effective_tz: {offenders}"
