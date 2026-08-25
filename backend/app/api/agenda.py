"""The agenda API: the Google connection, the panel's data, and tasks.

The consent flow is here rather than in a service because it is pure HTTP: a
redirect out, a redirect back, and one row written. Everything with a decision in
it lives in app/services/agenda.py, which the chat tools call too — so the panel
and the assistant can never disagree about what needs the rep's attention.

GET /api/agenda deliberately makes NO model call. It runs the same scoped service
functions the tools run, which keeps the panel fast and free, and means a scoping
bug shows up in both places rather than only one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from ..deps import AgendaRep, CurrentRep
from ..integrations.google import calendar as gcal
from ..integrations.google import oauth
from ..integrations.google.client import GoogleError
from ..services import agenda as agenda_service

router = APIRouter(prefix="/api/agenda", tags=["agenda"])
log = logging.getLogger(__name__)

_NOT_CONFIGURED = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Google integration is not configured on this server.",
)


def _back_to_app(outcome: str) -> RedirectResponse:
    """Where the rep's browser goes after the consent round trip.

    ABSOLUTE, and that is the bug this fixes. The API and the app are DIFFERENT
    ORIGINS in development — uvicorn on :8000, vite on :5173 — so a relative
    "/?agenda=connected" resolved against the API, which serves no page and
    answered a rep's successful Gmail connection with {"detail":"Not Found"}.
    The credential had been stored correctly; only the landing was wrong, which
    is the worst shape of bug to diagnose from outside: everything works and the
    last thing you see is a 404.

    The app's origin is read from the CORS setting rather than becoming a second
    env var that can disagree with the first — it is already required, and
    already correct in both shapes (:5173 in dev, :8080 behind nginx). An empty
    list means the app is served same-origin, where the relative form is right.

    It lands on Settings, not "/", because that is the page the rep left to give
    consent and the only page that shows the result. The path is the one place
    this file knows a frontend route; frontend/src/lib/routes.ts owns it.
    """
    base = settings.cors_origin_list[0].rstrip("/") if settings.cors_origin_list else ""
    return RedirectResponse(f"{base}/settings?agenda={outcome}", status_code=302)


# ---------------------------------------------------------------------------
# the connection
# ---------------------------------------------------------------------------


@router.get("/connection")
async def get_connection(rep: AgendaRep) -> dict:
    """What Settings shows: whether this rep has a mailbox connected, and which."""
    if not settings.agenda_configured:
        return {
            "configured": False,
            "connected": False,
            "stale": False,
            "email_account": None,
            "scopes": [],
            "why": (
                "This server has no Google client configured, so mail and calendar are "
                "unavailable. Tasks still work."
            ),
        }
    connection = await asyncio.to_thread(agenda_service.connection, rep.chair_id, rep.rep_code)
    stale = connection is not None and connection.stale
    return {
        "configured": True,
        # `and not stale` is the fix, not a refinement. A stale row is a row, so
        # `connection is not None` reported an expired connection as connected —
        # a green badge over a mailbox that answers 400 to everything.
        "connected": connection is not None and not stale,
        "stale": stale,
        "email_account": connection.email_account if connection else None,
        "scopes": list(connection.scopes) if connection else [],
        "calendar_tz": connection.calendar_tz if connection else None,
        "why": (
            "The connection to this account has expired — Google needs the rep's consent "
            "again. Reconnecting takes one click and replaces the stored credential."
            if stale
            else None
        ),
    }


@router.get("/connect")
async def connect(rep: CurrentRep) -> RedirectResponse:
    """Start the consent flow. 302 to Google."""
    if not settings.agenda_configured:
        raise _NOT_CONFIGURED
    return RedirectResponse(oauth.authorize_url(rep.chair_id), status_code=302)


@router.get("/callback")
async def callback(rep: CurrentRep, code: str = "", state: str = "") -> RedirectResponse:
    """Google sends the rep back here with a code.

    TWO identity checks, and both are needed:

      * `state` proves the flow was STARTED by someone holding a valid token for
        this chair. Without it, anyone who can make the rep's browser hit this
        endpoint carrying their own `code` connects THEIR mailbox to the rep's
        chair_id — the reverse of the usual login CSRF, and it would silently
        point the whole feature at an attacker-controlled inbox.
      * the session cookie (via CurrentRep) proves the person FINISHING it is the
        same rep. State alone is a bearer token for the callback.
    """
    if not settings.agenda_configured:
        raise _NOT_CONFIGURED
    if not code or not state:
        return _back_to_app("failed")
    try:
        state_chair = oauth.read_state(state)
    except ValueError:
        log.warning("agenda callback with an untrustworthy state")
        return _back_to_app("failed")
    if state_chair != rep.chair_id:
        log.warning("agenda callback chair mismatch", extra={"chair_id": rep.chair_id})
        return _back_to_app("failed")

    try:
        tokens = await oauth.exchange_code(code)
        refresh_token = str(tokens.get("refresh_token") or "")
        access_token = str(tokens.get("access_token") or "")
        if not refresh_token or not access_token:
            # Without a refresh token the connection works until the first
            # access token expires and then fails mysteriously. Refuse now.
            log.warning("google returned no refresh token")
            return _back_to_app("failed")
        profile = await oauth.userinfo(access_token)
        email_account = str(profile.get("email") or "").lower()
        if not email_account:
            return _back_to_app("failed")
        try:
            tz = await gcal.timezone(access_token)
        except GoogleError:
            tz = "UTC"
        await asyncio.to_thread(
            agenda_service.store_connection,
            chair_id=rep.chair_id,
            rep_code=rep.rep_code,
            email_account=email_account,
            # As GRANTED, not as requested: Google may return fewer.
            scopes=str(tokens.get("scope") or "").split(),
            calendar_tz=tz,
            refresh_token=refresh_token,
        )
    except (GoogleError, ValueError):
        log.warning("agenda connect failed", exc_info=True)
        return _back_to_app("failed")

    return _back_to_app("connected")


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(rep: CurrentRep) -> None:
    """Revoke at Google, then DELETE the row.

    The rep's tasks and the outbound compliance log deliberately survive: tasks
    are their own work, and the log is the record of what was sent to prescribers
    and who approved it.
    """
    await agenda_service.disconnect(rep)


# ---------------------------------------------------------------------------
# the panel
# ---------------------------------------------------------------------------


@router.get("")
async def get_agenda(rep: AgendaRep, days: int = 7) -> dict:
    """Everything the Agenda panel renders, in one call and with no model."""
    # "Today" in the REP'S zone, not the server's — task sections already use
    # rep_timezone, and the events_today count crossing midnight at a different
    # moment than the sections it sits beside is a silent disagreement.
    today = await asyncio.to_thread(
        lambda: datetime.now(agenda_service.rep_timezone(rep)).date()
    )
    tasks = await asyncio.to_thread(agenda_service.list_tasks, rep, status="open", limit=50)
    payload: dict = {
        "as_of": today.isoformat(),
        "connected": rep.email_account is not None,
        "configured": settings.agenda_configured,
        "mail": [],
        "calendar": [],
        "tasks": tasks,
        "counts": {},
        "mail_error": None,
        "mail_state": "live",
    }

    if rep.email_account is None:
        # Absent and stale both leave email_account None (deps.agenda_rep), so
        # ask once which it is — the panel must say "expired, reconnect" rather
        # than "not connected" to a rep who did connect. One query, and only on
        # the path that is doing no Gmail work anyway.
        payload["mail_state"] = await asyncio.to_thread(
            agenda_service.connection_state, rep.chair_id, rep.rep_code
        )
        payload["counts"] = {"tasks": len(tasks)}
        return payload

    try:
        items = await agenda_service.triage(rep, limit=40)
        payload["mail"] = [
            {
                "thread_id": i.thread_id,
                "subject": i.subject,
                "from_name": i.from_name,
                "from_address": i.from_address,
                "received_at": i.received_at,
                "days_waiting": i.days_waiting,
                "category": i.category,
                "reason": i.reason,
                "doctor_id": i.doctor_id,
                "doctor_name": i.doctor_name,
            }
            for i in items
        ]
    except agenda_service.NotConnected:
        payload["connected"] = False
    except (GoogleError, ValueError) as exc:
        # Reported in the payload rather than as a 500: the panel should still
        # render the calendar and the tasks.
        payload["mail_error"] = str(exc)

    try:
        payload["calendar"] = await agenda_service.events(
            rep, from_date=today, to_date=today + timedelta(days=max(1, min(days, 30)))
        )
    except (agenda_service.NotConnected, GoogleError, ValueError):
        payload["calendar"] = []

    mail = payload["mail"]
    payload["counts"] = {
        "needs_reply": sum(1 for m in mail if m["category"] == agenda_service.NEEDS_REPLY),
        "follow_up_due": sum(1 for m in mail if m["category"] == agenda_service.FOLLOW_UP_DUE),
        "escalate": sum(1 for m in mail if m["category"] == agenda_service.ESCALATE),
        "events_today": sum(1 for e in payload["calendar"] if str(e["start"])[:10] == today.isoformat()),
        "tasks": len(tasks),
    }
    return payload


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


class TaskCreate(BaseModel):
    """A new task, from the Agenda panel.

    A pydantic model rather than the raw `dict` this used to hand-parse. With two
    fields the hand-rolled version was merely verbose; with six it is where a bug
    would live, and `date`/`time` here do the parsing and the 422 for free.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    due_date: date | None = None
    due_time: time | None = None
    important: bool = False
    notes: str | None = Field(default=None, max_length=2000)
    doctor_id: int | None = None


class TaskPatch(BaseModel):
    """A change to a task. Every field optional, and UNSET means "leave alone".

    The distinction between absent and explicitly null is the whole reason this is
    a model: `{"important": true}` must not blank a due date it never mentioned,
    and `{"due_date": null}` must clear one. `model_fields_set` is what tells them
    apart — a plain dict cannot.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    due_date: date | None = None
    due_time: time | None = None
    important: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
    doctor_id: int | None = None
    #: Completion is separate: "done" is one operation with one meaning, and
    #: folding it into a general patch invites a request that both edits and
    #: completes a task with no obvious order.
    done: bool | None = None


@router.get("/tasks")
async def get_tasks(
    rep: CurrentRep,
    task_status: Annotated[str, Query(alias="status", pattern="^(open|done|all)$")] = "open",
    important: bool = False,
    source: Annotated[str | None, Query(pattern="^(rep|assistant)$")] = None,
    doctor_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict:
    """The panel's task browser: filtered server-side, sectioned, and counted.

    Server-side because `status=all` is unbounded — a client filtering a
    truncated list would show "no done tasks" when it means "none in the first
    hundred", which is a silent lie rather than a slow page.
    """
    rows = await asyncio.to_thread(
        agenda_service.list_tasks,
        rep,
        status=task_status,
        important_only=important,
        source=source,
        doctor_id=doctor_id,
        limit=limit,
    )
    return {
        "row_count": len(rows),
        "counts": agenda_service.task_counts(rows),
        "rows": rows,
        # The filter dropdown's options, taken from the tasks themselves rather
        # than from app.doctors: only doctors this rep already has tasks for can
        # appear, so the control cannot become a directory listing.
        "doctors": sorted(
            {
                (int(r["doctor_id"]), str(r["doctor_name"]))
                for r in rows
                if r.get("doctor_id") and r.get("doctor_name")
            },
            key=lambda pair: pair[1],
        ),
    }


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def add_task(rep: CurrentRep, body: TaskCreate) -> dict:
    if body.due_time is not None and body.due_date is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A due time needs a due date.",
        )
    return await asyncio.to_thread(
        agenda_service.create_task,
        rep,
        title=body.title.strip(),
        due_date=body.due_date,
        due_time=body.due_time,
        important=body.important,
        notes=body.notes,
        doctor_id=body.doctor_id,
        # Written by the rep in the panel, as opposed to inferred by the
        # assistant mid-conversation. Worth distinguishing.
        source="rep",
    )


@router.patch("/tasks/{task_id}")
async def patch_task(rep: CurrentRep, task_id: str, body: TaskPatch) -> dict:
    sent = body.model_fields_set
    if body.done is not None:
        changed = await asyncio.to_thread(
            agenda_service.set_task_done, rep, task_id=task_id, done=body.done
        )
        if not changed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    fields = {name: getattr(body, name) for name in sent if name != "done"}
    if fields:
        if fields.get("due_time") is not None and "due_date" not in fields:
            # The stored date has to be checked, because the CHECK constraint sees
            # the row after the update, not the patch.
            existing = await asyncio.to_thread(agenda_service.read_task, rep, task_id=task_id)
            if existing is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
            if existing["due_date"] is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "A due time needs a due date."
                )
        try:
            row = await asyncio.to_thread(
                agenda_service.update_task, rep, task_id=task_id, **fields
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        return row

    row = await asyncio.to_thread(agenda_service.read_task, rep, task_id=task_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_task(rep: CurrentRep, task_id: str) -> None:
    if not await asyncio.to_thread(agenda_service.delete_task, rep, task_id=task_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
