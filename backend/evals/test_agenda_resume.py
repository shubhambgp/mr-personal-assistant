"""The approval round trip, over real HTTP.

ENGINEERING_LOG 16's lesson, applied deliberately: the LangGraph migration passed
13/13 on an eval harness that drives the agent core directly, and still 500'd the
moment a browser sent a message. "The gate works" and "a rep can approve an
email" are different claims, and only the second one needs the transport.

So these tests go through the FastAPI app: the SSE contract, the durable pause,
the ownership check on resume, and the edit whitelist. The model is a scripted
fake and the gated tool is local to this module, so nothing here calls OpenAI or
Google — but everything between the HTTP boundary and the tool handler is real.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.bot import graph
from app.bot.context import RepContext
from app.core.security import issue_token
from app.tools.base import ToolRegistry

pytestmark = pytest.mark.requires_db

SENT: list[dict] = []

#: Captured before the fixture replaces them, so the wrappers call the real thing.
_real_run_turn = graph.run_turn
_real_resume_turn = graph.resume_turn


class _Bound:
    def __init__(self, scripted):
        self._scripted = scripted
        self._i = 0

    async def ainvoke(self, _messages, **_kwargs):
        message = self._scripted[min(self._i, len(self._scripted) - 1)]
        self._i += 1
        return message


class _FakeLLM:
    def __init__(self, scripted):
        self._bound = _Bound(scripted)

    def bind_tools(self, _tools, **_kwargs):
        return self._bound


class _MailProvider:
    """One gated tool, shaped like send_email and going nowhere."""

    name = "fake-agenda"

    def get_tools(self, ctx, conn):
        del ctx, conn

        async def send_email(thread_id=None, to=None, subject="", body=""):
            SENT.append({"thread_id": thread_id, "to": to, "subject": subject, "body": body})
            return json.dumps({"sent": True})

        return [
            {
                "name": "send_email",
                "description": "send mail",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": ["string", "null"]},
                        "to": {"type": ["string", "null"]},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["thread_id", "to", "subject", "body"],
                    "additionalProperties": False,
                },
                "handler": send_email,
                "requires_approval": True,
                "approval_editable": ("subject", "body"),
            }
        ]


DRAFT = AIMessage(
    content="I have prepared a reply for your approval.",
    tool_calls=[
        {
            "name": "send_email",
            "args": {
                "thread_id": None,
                "to": "dr.sharma@clinic.test",
                "subject": "Renal dosing, as promised",
                "body": "Dear Dr Sharma, the maximum is 20 mg once daily.",
            },
            "id": "call_1",
            "type": "tool_call",
        }
    ],
)
AFTER = AIMessage(content="Sent.")


def _frames(body: str) -> list[dict]:
    out = []
    for chunk in body.split("\n\n"):
        line = next((ln for ln in chunk.split("\n") if ln.startswith("data:")), None)
        if line:
            out.append(json.loads(line[5:].strip()))
    return out


#: The scripted model for the current test. A holder rather than a closure so the
#: app can be started ONCE for the module while each test still gets a fresh
#: script — seven app lifespans in a row wedged the run.
SCRIPT: dict = {"llm": None}


@pytest.fixture(scope="module")
def app_client():
    """The real app, started once, with a scripted model and one gated fake tool.

    MODULE-SCOPED deliberately. Starting and stopping the FastAPI lifespan per
    test opens and closes the database pools, the Qdrant store and the checkpoint
    pool seven times over, and the run blocked. One lifespan, seven tests.

    It also does NOT use the shared `db_pools` fixture: the lifespan calls
    bootstrap.close_resources() on the way out, which would close pools another
    module opened — so the pools are put back afterwards if they were open before.
    """
    from app import registry as registry_module
    from app.api import chat as chat_module
    from app.bot import db
    from app.main import app

    fake_registry = ToolRegistry([_MailProvider()])

    async def reviewer(*, calls, passages):
        """A scripted compliance verdict.

        Without this the REAL reviewer runs, which constructs a live ChatOpenAI
        and bills a request per gated round — found by watching this suite make
        an outbound call to api.openai.com. The reviewer's own logic is covered
        offline in tests/test_compliance_reviewer.py.
        """
        del calls, passages
        return {
            "verdict": "clear",
            "findings": [],
            "requires_escalation": None,
            "reviewed_by": "rules+model",
            "cited": True,
            "available": 0,
            "findings_dropped": 0,
        }

    async def run_turn(**kwargs):
        kwargs["reviewer"] = reviewer
        return await _real_run_turn(llm=SCRIPT["llm"], **kwargs)

    async def resume_turn(**kwargs):
        kwargs["reviewer"] = reviewer
        return await _real_resume_turn(llm=SCRIPT["llm"], **kwargs)

    # Patched by hand rather than with monkeypatch, which is function-scoped.
    saved = (chat_module.registry, registry_module.registry, graph.run_turn, graph.resume_turn)
    chat_module.registry = fake_registry
    registry_module.registry = fake_registry
    graph.run_turn = run_turn
    graph.resume_turn = resume_turn

    pools_were_open = True
    try:
        db.ro_pool()
    except RuntimeError:
        pools_were_open = False

    test_client = TestClient(app)
    try:
        test_client.__enter__()
    except Exception as exc:  # noqa: BLE001
        chat_module.registry, registry_module.registry, graph.run_turn, graph.resume_turn = saved
        reason = str(exc)
        if "already accessed by another instance" in reason:
            # Names the real cause rather than "needs a database". Qdrant's local
            # mode takes an exclusive folder lock, so a running dev server blocks
            # this whole module — and a skip that misattributes that costs
            # somebody an afternoon.
            pytest.skip(
                "the Qdrant store is locked by another process (a running uvicorn?). "
                "Local mode is single-process; stop the dev server, or set QDRANT_URL."
            )
        pytest.skip(f"app could not start (needs a loaded database): {reason}")

    try:
        with db.ro_pool().connection() as conn:
            row = conn.execute(
                "SELECT chair_id, rep_code, rep_name FROM reps ORDER BY chair_id LIMIT 1"
            ).fetchone()
        assert row, "reps table is empty — apply etl/seed_app.sql first"
        chair_id, rep_code, rep_name = row
        yield test_client, RepContext(chair_id=chair_id, rep_code=rep_code, rep_name=rep_name)
    finally:
        test_client.__exit__(None, None, None)
        chat_module.registry, registry_module.registry, graph.run_turn, graph.resume_turn = saved
        if pools_were_open:
            with contextlib.suppress(Exception):
                db.open_pools()


@pytest.fixture
def client(app_client):
    """A fresh script and a clean outbox for each test, on the shared app."""
    test_client, rep = app_client
    SENT.clear()
    SCRIPT["llm"] = _FakeLLM([DRAFT, AFTER])
    test_client.cookies.set(
        "qorvexa_session",
        issue_token(chair_id=rep.chair_id, rep_code=rep.rep_code, rep_name=rep.rep_name),
    )
    return test_client, rep


def _start(client) -> tuple[str, dict]:
    response = client.post(
        "/api/chat/stream", data={"message": "email Dr Sharma the renal dosing"}
    )
    assert response.status_code == 200, response.text
    frames = _frames(response.text)
    kinds = [f["type"] for f in frames]
    approval = next(f for f in frames if f["type"] == "approval_required")
    return approval["conversation_id"], {"frames": frames, "kinds": kinds, "approval": approval}


def test_a_gated_turn_ends_with_approval_required_and_never_says_done(client):
    """The turn has not produced an answer, so it must not claim to have.

    `done` carries usage and timing for a completed turn; emitting it here would
    tell the client the answer had arrived and would run record_turn, writing an
    assistant row that the resume leg would then duplicate.
    """
    test_client, _rep = client
    _convo_id, seen = _start(test_client)
    assert "approval_required" in seen["kinds"]
    assert "done" not in seen["kinds"], seen["kinds"]
    assert seen["approval"]["calls"][0]["name"] == "send_email"
    assert seen["approval"]["calls"][0]["editable"] == ["subject", "body"]
    assert seen["approval"]["interrupt_id"]
    assert SENT == [], "the handler ran before a human approved it"


def test_a_paused_turn_is_still_there_after_a_reload(client):
    """The interrupt lives in the graph checkpoint; the UI rebuilds from Postgres.

    With nothing persisted, a reload loses the card AND leaves the thread wedged:
    the next message re-enters a thread whose interrupted task is still pending,
    so it interrupts again immediately, forever.
    """
    test_client, _rep = client
    convo_id, seen = _start(test_client)

    reopened = test_client.get(f"/api/conversations/{convo_id}")
    assert reopened.status_code == 200
    assistant = [m for m in reopened.json()["messages"] if m["role"] == "assistant"]
    assert assistant, "the paused turn was not written to the transcript"
    pending = assistant[-1]["pending_approval"]
    assert pending is not None
    assert pending["interrupt_id"] == seen["approval"]["interrupt_id"]
    assert pending["calls"][0]["args"]["to"] == "dr.sharma@clinic.test"


def test_sending_another_message_re_presents_the_card_instead_of_wedging(client):
    """The guard for the failure above, from the other direction."""
    test_client, _rep = client
    convo_id, _seen = _start(test_client)
    again = test_client.post(
        "/api/chat/stream",
        data={"message": "actually, what about Dr Patil?", "conversation_id": convo_id},
    )
    kinds = [f["type"] for f in _frames(again.text)]
    assert "approval_required" in kinds
    assert "done" not in kinds
    assert SENT == []


def test_another_rep_cannot_resume_a_paused_approval(client):
    """A resume is a second entry point into a checkpointed thread.

    The first entry is guarded by get_or_create's (id, chair_id) filter. This is
    what guards the second — and it must 404 rather than 403, so "does not exist"
    and "is not yours" are indistinguishable from outside.
    """
    test_client, rep = client
    convo_id, seen = _start(test_client)

    intruder = issue_token(chair_id=rep.chair_id + 99_999, rep_code=999_999, rep_name="Rep B")
    test_client.cookies.set("qorvexa_session", intruder)
    refused = test_client.post(
        "/api/chat/resume",
        json={
            "conversation_id": convo_id,
            "interrupt_id": seen["approval"]["interrupt_id"],
            "approved": True,
            "edits": {},
        },
    )
    assert refused.status_code == 404, refused.text
    assert SENT == [], "another rep's approval sent the mail"


def test_a_stale_interrupt_id_is_refused(client):
    """A card left open in a second tab must not approve a superseded draft."""
    test_client, _rep = client
    convo_id, _seen = _start(test_client)
    refused = test_client.post(
        "/api/chat/resume",
        json={
            "conversation_id": convo_id,
            "interrupt_id": str(uuid.uuid4()),
            "approved": True,
            "edits": {},
        },
    )
    assert refused.status_code == 409, refused.text
    assert SENT == []


def test_a_second_concurrent_claim_of_the_same_card_is_refused(client):
    """The double-send guard (audit finding M-AI1).

    Two tabs — or one double-click the network retried — both reach the resume
    endpoint before either finishes. The atomic claim is what stops both from
    driving a resume and sending the same mail twice: the first claim wins, the
    second sees the card already claimed and returns None -> 409.
    """
    from app.services import conversations as convo_service

    test_client, rep = client
    convo_id, seen = _start(test_client)
    interrupt_id = seen["approval"]["interrupt_id"]

    first = convo_service.claim_pending_approval(rep, convo_id, interrupt_id)
    second = convo_service.claim_pending_approval(rep, convo_id, interrupt_id)
    assert first is not None, "the first claim should win"
    assert second is None, "a second claim within the window must be refused"


def test_approving_with_an_edit_sends_the_edit_and_keeps_the_recipient(client):
    """The whole feature, end to end — and the security rule inside it.

    An edit may change what is SAID and never who it is said TO. The hostile
    fields here are what an exfiltration attempt would look like through a
    channel the rep believes they are approving.
    """
    test_client, _rep = client
    convo_id, seen = _start(test_client)

    resumed = test_client.post(
        "/api/chat/resume",
        json={
            "conversation_id": convo_id,
            "interrupt_id": seen["approval"]["interrupt_id"],
            "approved": True,
            "edits": {
                "call_1": {
                    "body": "Dear Dr Sharma, rewritten by the rep.",
                    "to": "attacker@evil.test",
                    "thread_id": "some-other-thread",
                }
            },
        },
    )
    assert resumed.status_code == 200, resumed.text
    kinds = [f["type"] for f in _frames(resumed.text)]
    assert "done" in kinds, kinds

    assert len(SENT) == 1, SENT
    assert SENT[0]["body"] == "Dear Dr Sharma, rewritten by the rep."
    assert SENT[0]["to"] == "dr.sharma@clinic.test", "the recipient moved"
    assert SENT[0]["thread_id"] is None
    assert SENT[0]["subject"] == "Renal dosing, as promised", "an untouched field was lost"

    # And the pause is cleared, so the thread is usable again.
    reopened = test_client.get(f"/api/conversations/{convo_id}")
    assistant = [m for m in reopened.json()["messages"] if m["role"] == "assistant"]
    assert assistant[-1]["pending_approval"] is None


def test_rejecting_sends_nothing_and_clears_the_pause(client):
    test_client, _rep = client
    convo_id, seen = _start(test_client)
    resumed = test_client.post(
        "/api/chat/resume",
        json={
            "conversation_id": convo_id,
            "interrupt_id": seen["approval"]["interrupt_id"],
            "approved": False,
            "edits": {},
        },
    )
    assert resumed.status_code == 200, resumed.text
    assert SENT == []
    reopened = test_client.get(f"/api/conversations/{convo_id}")
    assistant = [m for m in reopened.json()["messages"] if m["role"] == "assistant"]
    assert assistant[-1]["pending_approval"] is None
