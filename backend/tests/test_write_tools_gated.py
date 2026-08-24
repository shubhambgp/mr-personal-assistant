"""Every tool that writes to Google pauses for a human, proved end to end.

The claim under test is not "the flag is set" — that is asserted next door in
test_tool_adapter.py, and a flag is only as good as the routing that reads it.
Here the REAL tool specs from AgendaToolProvider are driven through the REAL
graph, and the assertion is that **no HTTP request reached Google** while the turn
was paused, and none reached it at all when the rep said no.

That is proof by ABSENCE of the request rather than by a reassuring message. A
tool that reported "prepared for approval" and had already sent the mail would
pass a message-shaped test and fail this one.

The Google boundary is httpx.MockTransport; `_access_token` is stubbed so no
database or token is needed.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.bot.context import RepContext
from app.bot.graph import build_graph
from app.config import settings
from app.services import agenda as agenda_service
from app.tools.agenda_tools import GATED_TOOL_NAMES, AgendaToolProvider

pytestmark = pytest.mark.asyncio

CTX = RepContext(
    chair_id=7100001, rep_code=7800001, rep_name="Test Rep", email_account="rep@example.test"
)

CONNECTION = agenda_service.Connection(
    chair_id=7100001,
    rep_code=7800001,
    email_account="rep@example.test",
    scopes=(
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar.events",
    ),
    calendar_tz="Asia/Kolkata",
)

#: One representative call per gated tool, with arguments that would succeed.
#: `starts_at` is a fixed date so nothing here depends on the clock.
WRITE_CALLS = {
    "send_email": {
        "thread_id": "th-1",
        "to": None,
        "subject": "Following up",
        "body": "Thank you for your time.",
    },
    "create_event": {
        "title": "Call on Dr Sharma",
        "starts_at": "2026-09-01T15:00",
        "duration_minutes": 30,
        "attendees": [],
        "notes": "",
        "notify": False,
        "doctor_id": None,
    },
    "update_event": {
        "event_id": "ev-1",
        "title": None,
        "starts_at": "2026-09-02T16:00",
        "duration_minutes": 30,
        "notes": None,
    },
    "cancel_event": {"event_id": "ev-1"},
    "schedule_task": {
        "task_id": "00000000-0000-0000-0000-000000000001",
        "starts_at": "2026-09-01T09:00",
        "duration_minutes": 30,
    },
}

#: Methods that CHANGE something at Google. A GET while paused would be a leak of
#: a different kind, but it is not a write; these are.
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


@pytest.fixture
def google(monkeypatch):
    """Records every request that reaches Google, and answers plausibly."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if "/events/" in request.url.path and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "ev-1",
                    "summary": "Existing meeting",
                    "status": "confirmed",
                    "start": {"dateTime": "2026-09-01T15:00:00+05:30"},
                    "attendees": [{"email": "dr.sharma@example.test"}],
                    "description": "",
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "ev-new", "threadId": "th-1"})

    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return seen


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(
        settings, "agenda_encryption_key", base64.urlsafe_b64encode(b"\x01" * 32).decode()
    )

    async def token(_ctx):
        return "access-token", CONNECTION

    monkeypatch.setattr(agenda_service, "_access_token", token)
    # Recipient resolution and the outbound log are separately tested; here they
    # must simply not need a database.
    async def recipients(_ctx, *, thread_id, to):
        del thread_id, to
        return ["dr.sharma@example.test"], None, None

    monkeypatch.setattr(agenda_service, "resolve_recipients", recipients)
    monkeypatch.setattr(agenda_service, "record_outbound", lambda *_a, **_k: None)
    monkeypatch.setattr(agenda_service, "correspondents", _empty_set)
    monkeypatch.setattr(
        agenda_service,
        "read_task",
        lambda *_a, **_k: {
            "id": "00000000-0000-0000-0000-000000000001",
            "title": "Send the dosing card",
            "notes": None,
            "doctor_id": None,
            "done_at": None,
            "calendar_event_id": None,
        },
    )
    monkeypatch.setattr(agenda_service, "set_task_calendar_event", lambda *_a, **_k: None)
    monkeypatch.setattr(agenda_service, "_unlink_scheduled_task", lambda *_a, **_k: None)

    async def no_events(_ctx, **_k):
        return []

    monkeypatch.setattr(agenda_service, "events", no_events)

    async def detail(_ctx, *, thread_id):
        del thread_id
        return {"doctor_id": None, "messages": [{"body": "Thanks for the visit."}]}

    monkeypatch.setattr(agenda_service, "thread_detail", detail)


async def _empty_set(_ctx):
    return set()


class _Bound:
    def __init__(self, scripted):
        self._scripted, self._i = scripted, 0

    async def ainvoke(self, _messages, **_kwargs):
        msg = self._scripted[min(self._i, len(self._scripted) - 1)]
        self._i += 1
        return msg


class _FakeLLM:
    def __init__(self, scripted):
        self._bound = _Bound(scripted)

    def bind_tools(self, _tools, **_kwargs):
        return self._bound


async def _clean_review(**_kwargs) -> dict:
    """A scripted reviewer. Injected so no OpenAI call is ever made — the reviewer
    builds a live ChatOpenAI when nothing is passed, which is how an earlier
    version of this suite quietly reached the network."""
    return {
        "verdict": "pass",
        "findings": [],
        "requires_escalation": None,
        "reviewed_by": "test",
        "cited": True,
        "available": 0,
        "findings_dropped": 0,
    }


def _graph(tool_name: str, specs):
    wants = AIMessage(
        content="",
        tool_calls=[
            {"name": tool_name, "args": WRITE_CALLS[tool_name], "id": "c1", "type": "tool_call"}
        ],
    )
    return build_graph(
        llm=_FakeLLM([wants, AIMessage("Done.")]),  # type: ignore[arg-type]
        tool_specs=specs,
        instructions="rules",
        cache_key=CTX.cache_key(),
        reviewer=_clean_review,
    ).compile(checkpointer=InMemorySaver())


def _specs():
    return AgendaToolProvider().get_tools(CTX, conn=None)


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_every_gated_tool_has_a_call_scripted_here():
    """So adding a write tool without a gate test fails loudly rather than
    silently going uncovered."""
    assert set(WRITE_CALLS) == set(GATED_TOOL_NAMES)


@pytest.mark.parametrize("tool_name", sorted(GATED_TOOL_NAMES))
async def test_the_turn_pauses_and_nothing_reaches_google(tool_name, configured, google):
    graph = _graph(tool_name, _specs())
    config = {"configurable": {"thread_id": f"pause-{tool_name}", "rep": CTX}}

    result = await graph.ainvoke(
        {"messages": [("user", "do the thing")], "rounds": 0}, config=config
    )

    assert "__interrupt__" in result, f"{tool_name} did not pause the turn"
    assert result["__interrupt__"][0].value["reason"] == "approval_required"
    writes = [(m, p) for m, p in google if m in WRITE_METHODS]
    assert writes == [], f"{tool_name} wrote to Google before a human approved: {writes}"


@pytest.mark.parametrize("tool_name", sorted(GATED_TOOL_NAMES))
async def test_rejecting_never_reaches_google_at_all(tool_name, configured, google):
    graph = _graph(tool_name, _specs())
    config = {"configurable": {"thread_id": f"reject-{tool_name}", "rep": CTX}}

    await graph.ainvoke({"messages": [("user", "do the thing")], "rounds": 0}, config=config)
    await graph.ainvoke(Command(resume={"approved": False}), config=config)

    writes = [(m, p) for m, p in google if m in WRITE_METHODS]
    assert writes == [], f"{tool_name} wrote to Google after the rep declined: {writes}"


@pytest.mark.parametrize("tool_name", sorted(GATED_TOOL_NAMES))
async def test_approving_does_reach_google(tool_name, configured, google):
    """The other half. Without this, a gate that refused everything would pass."""
    graph = _graph(tool_name, _specs())
    config = {"configurable": {"thread_id": f"approve-{tool_name}", "rep": CTX}}

    await graph.ainvoke({"messages": [("user", "do the thing")], "rounds": 0}, config=config)
    await graph.ainvoke(Command(resume={"approved": True}), config=config)

    writes = [(m, p) for m, p in google if m in WRITE_METHODS]
    assert writes, f"{tool_name} was approved but never reached Google"


async def test_cancel_event_reviews_clean_with_no_invented_findings(configured, google):
    """A cancellation carries no text of ours — Google composes it. A reviewer
    asked to review nothing tends to find something, so the empty draft must not
    reach it at all."""
    from app.bot import compliance

    verdict = await compliance.review_calls(
        calls=[{"name": "cancel_event", "args": {"event_id": "ev-1"}}], passages=[]
    )
    assert verdict["verdict"] == "clear"
    assert verdict["findings"] == []
    assert verdict["reviewed_by"] == "not-outbound"


async def test_an_update_that_changes_only_the_time_is_not_sent_for_review(configured):
    """Same reason: `notes` is absent, so there is no draft."""
    from app.bot import compliance

    verdict = await compliance.review_calls(
        calls=[{"name": "update_event", "args": {"event_id": "ev-1", "starts_at": "2026-09-02T16:00"}}],
        passages=[],
    )
    assert verdict["reviewed_by"] == "not-outbound"


async def test_a_send_refuses_when_the_thread_cannot_be_read(configured, google, monkeypatch):
    """Fails CLOSED, and this is the rule worth failing closed on.

    check_outbound reads the thread to decide whether it is an adverse-event
    report. With no thread text the AE routing rules never fire — so continuing
    with thread_text="" (which is what the code used to do) could turn "do not
    comment on cause" into a sent reply doing exactly that. A Gmail hiccup must
    not be able to relax a pharmacovigilance rule.
    """

    async def unreadable(_ctx, *, thread_id):
        del thread_id
        raise RuntimeError("read-only pool not open")

    monkeypatch.setattr(agenda_service, "thread_detail", unreadable)

    out = json.loads(
        await agenda_service.send_mail(
            CTX, thread_id="th-1", to=None, subject="S", body="Thank you for your time."
        )
    )
    assert "Not sent" in out["error"]
    assert [m for m, _ in google if m in WRITE_METHODS] == []
