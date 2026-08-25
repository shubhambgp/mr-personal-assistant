"""Passages and the rep's edit reach the write path — proved through the real graph.

The bug this pins down: the send_email handler never passed the turn's
search_literature passages to services/agenda.send_mail, so the service's final
check_outbound ran with `retrieved=[]` and blocked EVERY draft containing a
clinical term plus a figure — including the one the reviewer had cleared against
those very passages and the rep had approved. Fail-closed, but the compliant
cited-clinical-email flow was structurally dead. And `edited_by_rep` was
hardcoded False at every record_outbound call site, in the one artefact whose
purpose is "what was sent, and did a human change it".

Both now travel out-of-band (app/bot/approval_context.py) — never as tool
parameters, because the model composes tool parameters and neither fact is the
model's to assert. These tests drive the REAL graph with a scripted model and
assert what the service actually received, in the style of
test_write_tools_gated.py.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.bot import guardrails
from app.bot.context import RepContext
from app.bot.graph import build_graph
from app.config import settings
from app.services import agenda as agenda_service
from app.tools.agenda_tools import AgendaToolProvider
from app.tools.base import ToolSpec

pytestmark = pytest.mark.asyncio

CTX = RepContext(
    chair_id=7100001, rep_code=7800001, rep_name="Test Rep", email_account="rep@example.test"
)

PASSAGE = {
    "document": "Cardevia (Cardevastatin) Monograph",
    "section": "4.2",
    "text": "Cardevia 20 mg reduced LDL-C by 38% at 12 weeks.",
}

CITED_BODY = "Cardevia reduced LDL by 38% at 12 weeks [Cardevia (Cardevastatin) Monograph — 4.2]."


def _retrieval_spec() -> ToolSpec:
    """A stand-in search_literature: same name, same output shape, no Qdrant."""

    async def search_literature(query: str) -> str:
        del query
        return json.dumps({"row_count": 1, "rows": [{**PASSAGE, "content": PASSAGE["text"]}]})

    return {
        "name": "search_literature",
        "description": "test stand-in",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": search_literature,
    }


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
    return {"verdict": "clear", "findings": [], "requires_escalation": None,
            "reviewed_by": "test", "cited": True, "available": 1, "findings_dropped": 0}


@pytest.fixture
def sent(monkeypatch):
    """The mail tools offered, and send_mail captured at the service boundary."""
    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(
        settings, "agenda_encryption_key",
        __import__("base64").urlsafe_b64encode(b"\x01" * 32).decode(),
    )
    captured: dict = {}

    async def fake_send_mail(_ctx, **kwargs):
        captured.update(kwargs)
        return json.dumps({"sent": True})

    monkeypatch.setattr(agenda_service, "send_mail", fake_send_mail)
    return captured


def _graph():
    """search_literature first, then a gated send_email, then a closing answer."""
    retrieve = AIMessage(
        content="",
        tool_calls=[{"name": "search_literature", "args": {"query": "LDL"}, "id": "c1",
                     "type": "tool_call"}],
    )
    send = AIMessage(
        content="",
        tool_calls=[{"name": "send_email",
                     "args": {"thread_id": "th-1", "to": None,
                              "subject": "Cardevia data", "body": CITED_BODY},
                     "id": "c2", "type": "tool_call"}],
    )
    specs = [
        _retrieval_spec(),
        *[s for s in AgendaToolProvider().get_tools(CTX, db=None) if s["name"] == "send_email"],
    ]
    return build_graph(
        llm=_FakeLLM([retrieve, send, AIMessage("Done.")]),  # type: ignore[arg-type]
        tool_specs=specs,
        instructions="rules",
        cache_key=CTX.cache_key(),
        reviewer=_clean_review,
    ).compile(checkpointer=InMemorySaver())


async def _drive(graph, thread_id: str, decision: dict) -> None:
    config = {"configurable": {"thread_id": thread_id, "rep": CTX}}
    result = await graph.ainvoke(
        {"messages": [("user", "email the LDL data")], "rounds": 0, "edited_call_ids": []},
        config=config,
    )
    assert "__interrupt__" in result, "the send was never gated"
    await graph.ainvoke(Command(resume=decision), config=config)


async def test_passages_retrieved_this_turn_reach_send_mail(sent):
    await _drive(_graph(), "passages", {"approved": True})

    assert sent, "send_mail was never reached after approval"
    docs = [p["document"] for p in sent["passages"]]
    assert docs == [PASSAGE["document"]], (
        "the turn's search_literature passages did not reach the service — "
        "its final check_outbound would treat every clinical claim as uncited"
    )
    assert sent["edited_by_rep"] is False


async def test_an_edit_at_the_gate_is_recorded_and_applied(sent):
    await _drive(_graph(), "edited", {"approved": True, "edits": {"c2": {"body": "Short note."}}})

    assert sent["body"] == "Short note.", "the approved edit was not what got sent"
    assert sent["edited_by_rep"] is True, (
        "the rep changed the draft at the gate but the service would log edited_by_rep=False"
    )


async def test_send_mail_clears_a_cited_claim_and_blocks_an_uncited_one(monkeypatch):
    """The service-level half: with passages the compliant draft sends, without
    them the same words are blocked — the fail-closed direction survives."""

    async def token(_ctx):
        return "tok", agenda_service.Connection(
            chair_id=CTX.chair_id, rep_code=CTX.rep_code, email_account="rep@example.test",
            scopes=("https://www.googleapis.com/auth/gmail.send",), calendar_tz="UTC",
        )

    async def recipients(_ctx, *, thread_id, to):
        del thread_id, to
        return ["dr.sharma@example.test"], "thread", None

    async def gmail_send(_token, **_kwargs):
        return {"id": "m-1"}

    monkeypatch.setattr(agenda_service, "_access_token", token)
    monkeypatch.setattr(agenda_service, "resolve_recipients", recipients)
    monkeypatch.setattr(agenda_service, "record_outbound", lambda *_a, **_k: None)
    monkeypatch.setattr(agenda_service.gmail, "send", gmail_send)

    with_passages = json.loads(await agenda_service.send_mail(
        CTX, thread_id=None, to="dr.sharma@example.test",
        subject="Cardevia data", body=CITED_BODY, passages=[PASSAGE],
    ))
    assert with_passages.get("sent") is True, with_passages

    without = json.loads(await agenda_service.send_mail(
        CTX, thread_id=None, to="dr.sharma@example.test",
        subject="Cardevia data", body=CITED_BODY, passages=[],
    ))
    assert "error" in without, "an uncited clinical claim must still be blocked"
    assert any(
        f["rule"] == "uncited_clinical_claim" for f in without.get("findings", [])
    ), without


async def test_the_deterministic_check_agrees_both_ways():
    """Belt and braces at the guardrails layer, no mocks at all."""
    cleared = guardrails.check_outbound(CITED_BODY, [PASSAGE])
    assert cleared["verdict"] == "clear", cleared
    blocked = guardrails.check_outbound(CITED_BODY, [])
    assert blocked["verdict"] == "block", blocked
