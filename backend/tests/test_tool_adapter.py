"""The adapter that lets LangGraph call our tools without changing them.

The point of these tests is that the migration did NOT move the security
boundary: the registry still rejects a scope parameter, and the adapter is a
thin, dumb translation downstream of it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from app.bot.context import RepContext
from app.bot.tool_adapter import (
    APPROVAL_KEY,
    editable_args,
    requires_approval,
    to_langchain_tools,
)
from app.registry import registry
from app.tools.base import ToolSpec

CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Test Rep")


def _spec(**over) -> ToolSpec:
    async def handler(**kwargs) -> str:
        return json.dumps({"ok": True, "got": kwargs})

    spec: ToolSpec = {
        "name": "demo",
        "description": "A demo tool.",
        "parameters": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        "handler": handler,
    }
    spec.update(over)  # type: ignore[typeddict-item]
    return spec


def test_every_real_tool_survives_adaptation():
    """All ten SQL tools convert, with names and schemas intact."""
    with_conn = registry.build(CTX, db=None)
    tools = to_langchain_tools(with_conn)

    assert len(tools) == len(with_conn)
    assert {t.name for t in tools} == {s["name"] for s in with_conn}
    for tool, spec in zip(tools, with_conn, strict=True):
        assert tool.description == spec["description"]
        # The raw JSON Schema is passed through, not re-derived. A regenerated
        # schema could quietly drop `additionalProperties: false` and lose
        # strict-mode compatibility.
        assert tool.args_schema is spec["parameters"]


def test_arguments_reach_the_handler_as_kwargs():
    tool = to_langchain_tools([_spec()])[0]
    out = json.loads(asyncio.run(tool.ainvoke({"q": "hello"})))
    assert out == {"ok": True, "got": {"q": "hello"}}


def test_a_raising_handler_becomes_a_json_error_not_an_exception():
    """CLAUDE.md: errors reaching the model are returned, never raised.

    A raised exception is an opaque tool failure; a returned `{"error": ...}` is
    something the model can read and explain to the rep. LangGraph's ToolNode
    would have caught it and produced its own wording, which the prompt has
    never been taught to read — so the adapter catches it first.
    """

    async def boom(**_kwargs) -> str:
        raise RuntimeError("database is on fire")

    tool = to_langchain_tools([_spec(name="boom", handler=boom)])[0]
    out = json.loads(asyncio.run(tool.ainvoke({"q": "x"})))
    assert "error" in out
    assert "database is on fire" in out["error"]


def test_approval_flag_defaults_to_false_and_round_trips():
    plain, gated = to_langchain_tools([_spec(), _spec(name="gated", **{APPROVAL_KEY: True})])
    assert requires_approval(plain) is False
    assert requires_approval(gated) is True


#: The only tools that reach outside the building.
#: Imported rather than restated: the module owns the list, and a copy here
#: would drift silently — which for this particular list means a write tool
#: reaching a prescriber with no human in front of it.
from app.tools.agenda_tools import GATED_TOOL_NAMES

GATED_TOOLS = set(GATED_TOOL_NAMES)


def _connected(monkeypatch):
    """A rep with a connected mailbox, and Google configured.

    Needed because AgendaToolProvider deliberately contributes NO mail tools when
    nothing is connected — which is why the gated tools are invisible in the
    default test environment and this has to be set up explicitly.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")
    monkeypatch.setattr(settings, "agenda_encryption_key", "0" * 43 + "=")
    return dataclasses.replace(CTX, email_account="rep@example.test")


def test_exactly_the_write_capable_tools_are_gated(monkeypatch):
    """The inverse of the assertion this replaces.

    It used to assert that NOTHING was gated, because every tool was read-only.
    Five tools now reach a prescriber's inbox and calendar, so the risk has
    flipped: what matters is a new write-capable tool arriving ungated, or a
    read-only tool acquiring a gate it does not need — which would train the rep
    to click through approvals and weaken the gate that counts.
    """
    tools = to_langchain_tools(registry.build(_connected(monkeypatch), db=None))
    assert {t.name for t in tools if requires_approval(t)} == GATED_TOOLS

    # Stated positively too, so a shrinking GATED_TOOL_NAMES cannot make this
    # test pass by agreeing with itself.
    assert sorted(GATED_TOOLS) == [
        "cancel_event",
        "create_event",
        "schedule_task",
        "send_email",
        "update_event",
    ]


def test_the_task_tools_are_deliberately_not_gated(monkeypatch):
    """A private to-do is not a regulated action and nothing leaves the app."""
    tools = to_langchain_tools(registry.build(_connected(monkeypatch), db=None))
    by_name = {t.name: t for t in tools}
    for name in ("create_task", "complete_task", "list_tasks"):
        assert not requires_approval(by_name[name]), name


def test_only_content_fields_are_editable_at_the_approval_gate(monkeypatch):
    """The whitelist IS the security boundary for an edit, so it is asserted both
    ways: what may be edited, and what must never appear in the list whatever a
    future provider declares."""
    tools = {t.name: t for t in to_langchain_tools(registry.build(_connected(monkeypatch), db=None))}
    assert set(editable_args(tools["send_email"])) == {"subject", "body"}
    # cancel_event has NOTHING editable: there is no content, only a decision.
    assert set(editable_args(tools["cancel_event"])) == set()
    assert set(editable_args(tools["update_event"])) == {
        "title",
        "starts_at",
        "duration_minutes",
        "notes",
    }
    # task_id is absent on purpose — retargeting which task is scheduled at the
    # gate would mean approving one thing and performing another.
    assert set(editable_args(tools["schedule_task"])) == {"starts_at", "duration_minutes"}
    assert set(editable_args(tools["create_event"])) == {
        "title",
        "starts_at",
        "duration_minutes",
        "notes",
    }
    never = {"to", "cc", "bcc", "thread_id", "mailbox", "email_account", "attendees", "notify"}
    for name in GATED_TOOLS:
        assert not never & set(editable_args(tools[name])), name


def test_an_ungated_tool_has_no_editable_fields(monkeypatch):
    """Nothing to approve means nothing to edit. Absent by default, so a newly
    gated tool is read-only until someone deliberately says otherwise."""
    tools = to_langchain_tools(registry.build(_connected(monkeypatch), db=None))
    for tool in tools:
        if not requires_approval(tool):
            assert editable_args(tool) == (), tool.name


def test_the_registry_still_rejects_a_scope_parameter():
    """The invariant the adapter must not have moved.

    Guarded here as well as in test_tool_registry.py on purpose: this is the
    check the LangGraph migration could most plausibly have bypassed by
    rewriting tools as @tool functions instead of adapting ToolSpecs.
    """
    from app.tools.base import ToolRegistry

    class Bad:
        name = "bad"

        def get_tools(self, ctx, conn):
            return [_spec(name="leaky", parameters={
                "type": "object",
                "properties": {"chair_id": {"type": "integer"}},
                "required": ["chair_id"],
                "additionalProperties": False,
            })]

    with pytest.raises(ValueError, match="chair_id"):
        ToolRegistry([Bad()]).build(CTX, db=None)
