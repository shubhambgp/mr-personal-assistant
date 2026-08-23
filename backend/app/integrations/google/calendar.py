"""Google Calendar REST calls: the primary calendar, single events, nothing else."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote

from .client import request

API = "https://www.googleapis.com/calendar/v3"


@dataclass
class Event:
    event_id: str
    title: str
    start: str
    end: str
    location: str
    attendees: list[str]
    organiser_is_me: bool
    all_day: bool


async def timezone(access_token: str) -> str:
    """The rep's own calendar timezone.

    Read once at connect time and stored, because every event write needs an
    IANA zone and guessing one books the meeting at the wrong hour — a failure
    the rep discovers by missing it.
    """
    payload = await request(
        "GET", f"{API}/users/me/settings/timezone", access_token=access_token
    )
    return str(payload.get("value") or "UTC")


def _when(node: dict) -> tuple[str, bool]:
    if value := node.get("dateTime"):
        return str(value), False
    return str(node.get("date") or ""), True


async def list_events(
    access_token: str, *, from_date: date, to_date: date, tz: str, limit: int = 50
) -> list[Event]:
    payload = await request(
        "GET",
        f"{API}/calendars/primary/events",
        access_token=access_token,
        params={
            "timeMin": datetime.combine(from_date, datetime.min.time()).isoformat() + "Z",
            "timeMax": datetime.combine(to_date + timedelta(days=1), datetime.min.time()).isoformat()
            + "Z",
            # Recurring events are expanded into their instances: a rep asking
            # "what's on Thursday" means occurrences, not rules.
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(limit, 250)),
            "timeZone": tz,
        },
    )
    events: list[Event] = []
    for item in payload.get("items") or []:
        if item.get("status") == "cancelled":
            continue
        start, all_day = _when(item.get("start") or {})
        end, _ = _when(item.get("end") or {})
        events.append(
            Event(
                event_id=str(item.get("id") or ""),
                title=str(item.get("summary") or "(no title)"),
                start=start,
                end=end,
                location=str(item.get("location") or ""),
                attendees=[
                    str(a.get("email"))
                    for a in (item.get("attendees") or [])
                    if a.get("email") and not a.get("self")
                ],
                organiser_is_me=bool((item.get("organizer") or {}).get("self")),
                all_day=all_day,
            )
        )
    return events


async def create_event(
    access_token: str,
    *,
    title: str,
    starts_at: datetime,
    duration_minutes: int,
    attendees: list[str],
    notes: str,
    tz: str,
    notify: bool,
) -> dict:
    body = {
        "summary": title,
        "description": notes or "",
        "start": {"dateTime": starts_at.isoformat(), "timeZone": tz},
        "end": {
            "dateTime": (starts_at + timedelta(minutes=duration_minutes)).isoformat(),
            "timeZone": tz,
        },
        "attendees": [{"email": a} for a in attendees],
    }
    return await request(
        "POST",
        f"{API}/calendars/primary/events",
        access_token=access_token,
        # `sendUpdates` is the difference between putting something on your own
        # calendar and emailing a doctor an invitation. Defaulting it to "none"
        # keeps an accidental invite out of a prescriber's inbox.
        params={"sendUpdates": "all" if notify else "none"},
        json_body=body,
    )


async def get_event(access_token: str, *, event_id: str) -> dict:
    """One event from the rep's own primary calendar.

    Exists so a write can be resolved against a real event before a human is
    asked to approve it: `primary` scopes the lookup to this rep, so an id the
    model invented — or copied out of a mail body — resolves to nothing rather
    than to somebody else's meeting.
    """
    return await request(
        "GET",
        f"{API}/calendars/primary/events/{quote(event_id, safe='')}",
        access_token=access_token,
    )


async def update_event(
    access_token: str,
    *,
    event_id: str,
    tz: str,
    notify: bool,
    title: str | None = None,
    starts_at: datetime | None = None,
    duration_minutes: int | None = None,
    notes: str | None = None,
) -> dict:
    """PATCH, not PUT, so unspecified fields keep their values.

    PUT on this endpoint replaces the whole event: changing only the time with
    PUT would silently drop the attendees, the description and the location. Only
    keys the caller actually passed are sent.
    """
    body: dict = {}
    if title is not None:
        body["summary"] = title
    if notes is not None:
        body["description"] = notes
    if starts_at is not None:
        body["start"] = {"dateTime": starts_at.isoformat(), "timeZone": tz}
        # Google keeps the old `end` unless it is sent too, so a later start with
        # no new end produces an event that ends before it begins — accepted by
        # the API and nonsense on the rep's calendar.
        minutes = duration_minutes if duration_minutes else 30
        body["end"] = {
            "dateTime": (starts_at + timedelta(minutes=minutes)).isoformat(),
            "timeZone": tz,
        }
    return await request(
        "PATCH",
        f"{API}/calendars/primary/events/{quote(event_id, safe='')}",
        access_token=access_token,
        params={"sendUpdates": "all" if notify else "none"},
        json_body=body,
    )


async def delete_event(access_token: str, *, event_id: str, notify: bool) -> None:
    """Cancel an event. Google mails the attendees when notify is true.

    A 404 or 410 is treated as success by the caller: cancelling something that
    is already cancelled is a race, not a failure.
    """
    await request(
        "DELETE",
        f"{API}/calendars/primary/events/{quote(event_id, safe='')}",
        access_token=access_token,
        params={"sendUpdates": "all" if notify else "none"},
    )
