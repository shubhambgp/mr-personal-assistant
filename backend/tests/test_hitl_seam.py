"""Human-in-the-loop: the reason the agent core moved to LangGraph at all.

No longer hypothetical. `send_email` and `create_event` reach outside the
building, so this gate is now the only thing between a model-composed draft and
a prescriber's inbox — and the interesting cases are the ones where it could be
talked around: an edit that changes the recipient rather than the wording, a
refusal that reports itself inaccurately, a reviewer whose verdict differs from
the one the rep approved.

These tests use a scripted fake model and an in-memory checkpointer, so they
exercise the real graph topology — routing, interrupt, resume — with no API call
and no database. `build_graph` taking `llm` as a parameter is what makes that
possible, which is a large part of why it does.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.bot.context import RepContext
from app.bot.graph import build_graph, run_turn
from app.tools.base import ToolSpec

CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Test Rep")

pytestmark = pytest.mark.asyncio


class _Bound:
    """Returns scripted replies in order, ignoring the conversation."""

    def __init__(self, scripted: list[AIMessage]) -> None:
        self._scripted = scripted
        self._i = 0

    async def ainvoke(self, _messages, **_kwargs):
        msg = self._scripted[min(self._i, len(self._scripted) - 1)]
        self._i += 1
        return msg


class _FakeLLM:
    def __init__(self, scripted: list[AIMessage]) -> None:
        self._bound = _Bound(scripted)

    def bind_tools(self, _tools, **_kwargs):
        return self._bound


CALLS: list[dict] = []


def _tool(name: str, *, gated: bool) -> ToolSpec:
    async def handler(**kwargs) -> str:
        CALLS.append({"name": name, "args": kwargs})
        return json.dumps({"sent": True})

    spec: ToolSpec = {
        "name": name,
        "description": f"{name} (gated={gated})",
        "parameters": {
            "type": "object",
            "properties": {"to": {"type": "string"}},
            "required": ["to"],
            "additionalProperties": False,
        },
        "handler": handler,
    }
    if gated:
        spec["requires_approval"] = True
    return spec


def _graph(scripted: list[AIMessage], specs: list[ToolSpec]):
    return build_graph(
        llm=_FakeLLM(scripted),  # type: ignore[arg-type]
        tool_specs=specs,
        instructions="rules",
        cache_key=CTX.cache_key(),
    ).compile(checkpointer=InMemorySaver())


def _wants(name: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"to": "dr.sharma@example.test"}, "id": call_id, "type": "tool_call"}],
    )


async def test_a_gated_tool_interrupts_before_the_handler_runs():
    CALLS.clear()
    graph = _graph([_wants("send_email", "c1"), AIMessage("Sent.")], [_tool("send_email", gated=True)])
    config = {"configurable": {"thread_id": "t-interrupt", "rep": CTX}}

    result = await graph.ainvoke({"messages": [("user", "email Dr Sharma")], "rounds": 0}, config=config)

    assert "__interrupt__" in result, "a gated tool did not pause the turn"
    payload = result["__interrupt__"][0].value
    assert payload["reason"] == "approval_required"
    assert payload["calls"][0]["name"] == "send_email"
    # The whole point: the side effect has NOT happened yet.
    assert CALLS == [], "the handler ran before a human approved it"


async def test_resuming_with_approval_runs_the_tool():
    CALLS.clear()
    graph = _graph([_wants("send_email", "c1"), AIMessage("Sent.")], [_tool("send_email", gated=True)])
    config = {"configurable": {"thread_id": "t-approve", "rep": CTX}}

    await graph.ainvoke({"messages": [("user", "email Dr Sharma")], "rounds": 0}, config=config)
    final = await graph.ainvoke(Command(resume={"approved": True}), config=config)

    assert [c["name"] for c in CALLS] == ["send_email"]
    assert final["messages"][-1].content == "Sent."


async def test_resuming_with_a_refusal_does_not_run_the_tool():
    """A declined action must leave the transcript valid, not dangling.

    The tool call still needs an answer — OpenAI rejects a follow-up turn whose
    history contains a tool call with no output — so a refusal is recorded as a
    ToolMessage the model can then explain to the rep.
    """
    CALLS.clear()
    graph = _graph([_wants("send_email", "c1"), AIMessage("Understood, not sent.")],
                   [_tool("send_email", gated=True)])
    config = {"configurable": {"thread_id": "t-refuse", "rep": CTX}}

    await graph.ainvoke({"messages": [("user", "email Dr Sharma")], "rounds": 0}, config=config)
    final = await graph.ainvoke(Command(resume={"approved": False}), config=config)

    assert CALLS == [], "the tool ran despite being declined"
    tool_messages = [m for m in final["messages"] if m.__class__.__name__ == "ToolMessage"]
    assert tool_messages, "the declined tool call was left unanswered"
    assert "declined" in tool_messages[-1].content


async def test_an_ungated_tool_runs_without_interruption():
    """The read-only case, which is every tool in the app today."""
    CALLS.clear()
    graph = _graph([_wants("find_doctor", "c1"), AIMessage("Found them.")],
                   [_tool("find_doctor", gated=False)])
    config = {"configurable": {"thread_id": "t-plain", "rep": CTX}}

    result = await graph.ainvoke({"messages": [("user", "find Dr Sharma")], "rounds": 0}, config=config)

    assert "__interrupt__" not in result
    assert [c["name"] for c in CALLS] == ["find_doctor"]
    assert result["messages"][-1].content == "Found them."


async def test_the_tool_round_cap_answers_its_pending_calls():
    """The backstop against a looping model, and why it must not leave a mess.

    The old loop could simply `break`: continuity was an OpenAI
    `previous_response_id`, so the abandoned tool call was never re-sent. The
    graph keeps the transcript itself, and OpenAI rejects a turn whose history
    contains a tool call with no output — so stopping at the cap has to answer
    the outstanding calls or it breaks the *next* message in the thread.
    """
    from app.bot.agent import MAX_TOOL_ROUNDS

    CALLS.clear()
    # A model that never stops asking for the tool.
    forever = [_wants("find_doctor", f"c{i}") for i in range(MAX_TOOL_ROUNDS + 3)]
    graph = _graph(forever, [_tool("find_doctor", gated=False)])
    config = {"configurable": {"thread_id": "t-cap", "rep": CTX}}

    result = await graph.ainvoke(
        {"messages": [("user", "loop please")], "rounds": 0},
        config=config,
    )

    assert len(CALLS) <= MAX_TOOL_ROUNDS, "the cap did not stop the loop"

    # Every tool call in the transcript has a matching output.
    requested = {
        c["id"]
        for m in result["messages"]
        if isinstance(m, AIMessage)
        for c in (m.tool_calls or [])
    }
    answered = {
        m.tool_call_id for m in result["messages"] if m.__class__.__name__ == "ToolMessage"
    }
    assert requested == answered, f"unanswered tool calls would break the next turn: {requested - answered}"


# ---------------------------------------------------------------------------
# The three agents, the reviewer, and the edit whitelist
# ---------------------------------------------------------------------------


def _email_tool(*, gated: bool = True, editable: tuple[str, ...] = ("subject", "body")) -> ToolSpec:
    """A send_email-shaped tool: a recipient that must not move, content that may."""

    async def handler(**kwargs) -> str:
        CALLS.append({"name": "send_email", "args": kwargs})
        return json.dumps({"sent": True})

    spec: ToolSpec = {
        "name": "send_email",
        "description": "send mail",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
        "handler": handler,
    }
    if gated:
        spec["requires_approval"] = True
    if editable:
        spec["approval_editable"] = editable
    return spec


def _plain_tool(name: str) -> ToolSpec:
    async def handler(**kwargs) -> str:
        CALLS.append({"name": name, "args": kwargs})
        return json.dumps({"ok": True})

    return {
        "name": name,
        "description": name,
        "parameters": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        "handler": handler,
    }


def _draft(call_id: str = "c1", **args) -> AIMessage:
    payload = {"to": "dr.sharma@clinic.test", "subject": "Renal dosing", "body": "Dear Doctor,"}
    payload.update(args)
    return AIMessage(
        content="",
        tool_calls=[{"name": "send_email", "args": payload, "id": call_id, "type": "tool_call"}],
    )


async def test_the_reviewer_runs_before_the_human_sees_the_card():
    """The verdict the rep approves must be the verdict that was computed.

    A node re-executes from its start on resume, so a reviewer living inside the
    approval node would run a SECOND time and could return something different
    from what the rep was shown — and then the audit record would describe a
    review that never happened. `route` sends a gated round to `review` and
    never straight to `approval`, which is what this asserts.
    """
    CALLS.clear()
    seen: list[dict] = []

    async def reviewer(*, calls, passages):
        seen.append({"calls": calls, "passages": passages})
        return {"verdict": "warn", "findings": ["Unsupported renal cut-off."]}

    graph = build_graph(
        llm=_FakeLLM([_draft(), AIMessage(content="Sent.")]),  # type: ignore[arg-type]
        tool_specs=[_email_tool()],
        instructions="rules",
        cache_key=CTX.cache_key(),
        reviewer=reviewer,
    ).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-review"}}

    paused = await graph.ainvoke({"messages": [], "rounds": 0}, config=config)
    payload = paused["__interrupt__"][0].value
    assert payload["review"] == {"verdict": "warn", "findings": ["Unsupported renal cut-off."]}
    assert payload["calls"][0]["editable"] == ["subject", "body"]
    assert CALLS == [], "the handler must not run before a human approves"

    await graph.ainvoke(Command(resume={"approved": True}), config=config)
    assert len(seen) == 1, "the reviewer ran again on resume; its verdict could disagree"


async def test_an_edit_changes_the_body_the_handler_receives():
    """Edit-then-send: the rep rewrites the wording and approves it."""
    CALLS.clear()
    graph = build_graph(
        llm=_FakeLLM([_draft(), AIMessage(content="Sent.")]),  # type: ignore[arg-type]
        tool_specs=[_email_tool()],
        instructions="rules",
        cache_key=CTX.cache_key(),
    ).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-edit"}}

    await graph.ainvoke({"messages": [], "rounds": 0}, config=config)
    await graph.ainvoke(
        Command(resume={"approved": True, "edits": {"c1": {"body": "Rewritten by the rep."}}}),
        config=config,
    )
    assert len(CALLS) == 1
    assert CALLS[0]["args"]["body"] == "Rewritten by the rep."
    assert CALLS[0]["args"]["subject"] == "Renal dosing", "an untouched field must survive"


async def test_an_edit_cannot_change_the_recipient():
    """An edit may change what is SAID, never who it is said TO.

    Without the whitelist the approval card becomes an exfiltration channel: the
    model asks to send to Dr Sharma, the rep sees Dr Sharma and clicks approve,
    and a modified payload delivers somewhere else. `chair_id` is in the same
    edit here because a field the model never declared must not become settable
    just because a human is nominally in the loop.
    """
    CALLS.clear()
    graph = build_graph(
        llm=_FakeLLM([_draft(), AIMessage(content="Sent.")]),  # type: ignore[arg-type]
        tool_specs=[_email_tool()],
        instructions="rules",
        cache_key=CTX.cache_key(),
    ).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-hostile"}}

    await graph.ainvoke({"messages": [], "rounds": 0}, config=config)
    await graph.ainvoke(
        Command(
            resume={
                "approved": True,
                "edits": {
                    "c1": {
                        "to": "attacker@evil.test",
                        "chair_id": "9999",
                        "body": "Edited body.",
                    }
                },
            }
        ),
        config=config,
    )
    assert len(CALLS) == 1
    args = CALLS[0]["args"]
    assert args["to"] == "dr.sharma@clinic.test", "the recipient moved"
    assert "chair_id" not in args
    assert args["body"] == "Edited body."


async def test_a_hallucinated_tool_name_does_not_crash_the_approval_node():
    """`by_name[...]` used to raise KeyError inside a node.

    An unhandled exception in a node reaches the rep as the generic "something
    went wrong" that CLAUDE.md §4 exists to prevent. `route` had the guard and
    the approval node did not.
    """
    CALLS.clear()
    hallucinated = AIMessage(
        content="",
        tool_calls=[{"name": "no_such_tool", "args": {}, "id": "c9", "type": "tool_call"}],
    )
    graph = build_graph(
        llm=_FakeLLM([hallucinated, AIMessage(content="Sorry.")]),  # type: ignore[arg-type]
        tool_specs=[_email_tool()],
        instructions="rules",
        cache_key=CTX.cache_key(),
    ).compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {"messages": [], "rounds": 0}, config={"configurable": {"thread_id": "t-ghost"}}
    )
    # It routes to `tools`, which reports the unknown name as a tool error. What
    # matters is that nothing raised and the turn still finished.
    assert "__interrupt__" not in result


async def test_a_declined_mixed_round_answers_every_call_and_lies_about_none():
    """A refusal must be accurate per call, not blanket.

    Every pending call still has to be answered — OpenAI rejects a follow-up turn
    whose history holds a tool call with no output. But this used to tell the
    model the rep had declined a read-only call the rep was never shown, so the
    model would explain a refusal that never happened.
    """
    CALLS.clear()
    mixed = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "send_email",
                "args": {"to": "d@x.test", "subject": "s", "body": "b"},
                "id": "gated",
                "type": "tool_call",
            },
            {"name": "list_mail", "args": {"q": "inbox"}, "id": "readonly", "type": "tool_call"},
        ],
    )
    graph = build_graph(
        llm=_FakeLLM([mixed, AIMessage(content="Understood.")]),  # type: ignore[arg-type]
        tool_specs=[_email_tool(), _plain_tool("list_mail")],
        instructions="rules",
        cache_key=CTX.cache_key(),
    ).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-mixed"}}

    await graph.ainvoke({"messages": [], "rounds": 0}, config=config)
    final = await graph.ainvoke(Command(resume={"approved": False}), config=config)

    answered = {m.tool_call_id: m.content for m in final["messages"] if hasattr(m, "tool_call_id")}
    assert set(answered) == {"gated", "readonly"}, "every call must be answered"
    assert "The rep declined to approve this action" in answered["gated"]
    # Asserted positively. A `"declined" not in ...` ban would fail on the
    # correct message, which says this call was not run BECAUSE another one was
    # declined — a substring test cannot tell making a claim from describing one.
    assert "Not run" in answered["readonly"]
    assert "The rep declined to approve this action" not in answered["readonly"]
    assert CALLS == []


async def test_an_interrupted_turn_reports_itself_in_the_turn_result():
    """The transport must be able to tell a pause from a finished answer.

    The updates stream yields {"__interrupt__": (Interrupt(...),)}, whose value
    is a TUPLE. The reader used to call .get("messages") on every delta, so the
    first real interrupt raised AttributeError — the rep got a generic error and
    the thread was left wedged at a pending interrupt with no way to resume.
    """
    CALLS.clear()
    deltas: list[str] = []

    async def on_text_delta(d):
        deltas.append(d)

    async def noop(*_a, **_k):
        return None

    result = await run_turn(
        ctx=CTX,
        tool_specs=[_email_tool()],
        user_message="email Dr Sharma the renal dosing",
        thread_id="t-turnresult",
        vintage_summary="2026-08",
        on_text_delta=on_text_delta,
        on_tool_start=noop,
        on_tool_end=noop,
        checkpointer=InMemorySaver(),
        llm=_FakeLLM([_draft()]),
        reviewer=None,
    )
    assert result.interrupt is not None
    assert result.interrupt["reason"] == "approval_required"
    assert result.interrupt["interrupt_id"]
    assert result.interrupt["calls"][0]["name"] == "send_email"
    assert CALLS == []


async def test_the_handoff_reaches_the_agenda_agent_and_its_tokens_are_not_dropped():
    """The agenda agent's prose must reach the rep.

    The reader used to keep only `langgraph_node == "agent"`, so a second agent's
    tokens were silently discarded and it appeared to answer with total silence.
    The filter is an allowlist now, and this is what holds it there.
    """
    CALLS.clear()
    handoff = AIMessage(
        content="",
        tool_calls=[
            {"name": "open_agenda", "args": {"task": "triage"}, "id": "h1", "type": "tool_call"}
        ],
    )
    deltas: list[str] = []

    async def on_text_delta(d):
        deltas.append(d)

    async def noop(*_a, **_k):
        return None

    result = await run_turn(
        ctx=CTX,
        tool_specs=[_plain_tool_named("open_agenda"), _plain_tool("list_mail")],
        user_message="what needs my attention?",
        thread_id="t-handoff",
        vintage_summary="2026-08",
        on_text_delta=on_text_delta,
        on_tool_start=noop,
        on_tool_end=noop,
        checkpointer=InMemorySaver(),
        llm=_FakeLLM([handoff, AIMessage(content="Three mails need a reply.")]),
        agenda_instructions="you are the agenda agent",
        agenda_tools=frozenset({"open_agenda", "list_mail"}),
    )
    assert [c["name"] for c in CALLS] == ["open_agenda"]
    assert "Three mails need a reply." in result.final_text
    assert "Three mails need a reply." in "".join(deltas), "the agenda agent streamed nothing"


def _plain_tool_named(name: str) -> ToolSpec:
    """The handoff tool: an ordinary ungated tool whose handler does nothing.

    That is the whole mechanism — ToolNode answers it, so the transcript is never
    left with an unanswered call, and the `tools ->` conditional edge does the
    routing.
    """

    async def handler(**kwargs) -> str:
        CALLS.append({"name": name, "args": kwargs})
        return json.dumps({"handed_off": True})

    return {
        "name": name,
        "description": name,
        "parameters": {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
            "additionalProperties": False,
        },
        "handler": handler,
    }
