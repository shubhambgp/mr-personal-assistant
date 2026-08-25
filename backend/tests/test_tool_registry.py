"""The registry's job is to compose providers and refuse two specific mistakes.

These run with no database: SqlToolProvider only touches the connection inside
handler bodies, so building the specs needs nothing live.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.bot.context import RepContext
from app.registry import registry
from app.tools.agenda_tools import AgendaToolProvider
from app.tools.base import ToolRegistry, forbidden_names_in
from app.tools.mcp_tools import McpToolProvider
from app.tools.rag_tools import RagToolProvider
from app.tools.sql_tools import SqlToolProvider

CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Test Rep")

EXPECTED_TOOLS = {
    "find_doctor",
    "get_doctor_brief",
    "get_doctor_hooks",
    "get_doctor_brands",
    "list_pending_visits",
    "get_visit_summary",
    "get_rep_scorecard",
    "get_doctor_chemists",
    "get_daily_plan",
    "run_sql",
}


def test_sql_provider_exposes_the_expected_tools():
    specs = SqlToolProvider().get_tools(CTX, db=None)
    assert {s["name"] for s in specs} == EXPECTED_TOOLS


#: search_literature ranks passages; read_document fetches one whole document
#: (the "what is in this PDF" question has no searchable terms); list_documents
#: names what is available.
RAG_TOOLS = {"search_literature", "list_documents", "read_document"}


def test_the_mcp_stub_composes_without_changing_the_tool_list():
    """The seam the email provider will land on, exercised while still empty.

    RAG used to be asserted here too. It now contributes real tools — which is
    what test_rag_provider_contributes_exactly_its_own_tools covers — so keeping
    it in this assertion would have meant weakening the check to match. The MCP
    seam is still genuinely empty, so it still gets the strict version.
    """
    sql_only = ToolRegistry([SqlToolProvider()]).build(CTX, db=None)
    with_stub = ToolRegistry([SqlToolProvider(), McpToolProvider()]).build(CTX, db=None)
    assert [s["name"] for s in sql_only] == [s["name"] for s in with_stub]


def test_rag_provider_contributes_exactly_its_own_tools():
    """Composition still works now that a real provider has landed on the seam."""
    sql_only = {s["name"] for s in ToolRegistry([SqlToolProvider()]).build(CTX, db=None)}
    composed = {
        s["name"]
        for s in ToolRegistry(
            [SqlToolProvider(), RagToolProvider(), McpToolProvider()]
        ).build(CTX, db=None)
    }
    assert composed - sql_only == RAG_TOOLS
    assert composed == EXPECTED_TOOLS | RAG_TOOLS


def test_rag_tools_do_not_accept_a_scope_parameter():
    """The invariant matters more here, not less.

    Retrieval has no SQL backstop — Qdrant is the only store — so the payload
    filter built from RepContext is the entire tenancy boundary. A `chair_id`
    parameter on one of these tools would hand that boundary to the model.
    """
    for spec in RagToolProvider().get_tools(CTX, db=None):
        props = set(spec["parameters"].get("properties", {}))
        assert not props & {"chair_id", "rep_id", "rep_code"}, spec["name"]


def test_duplicate_tool_name_is_rejected():
    """A remote MCP server must not be able to shadow one of our tools."""
    registry = ToolRegistry([SqlToolProvider(), SqlToolProvider()])
    with pytest.raises(ValueError, match="duplicate tool name"):
        registry.build(CTX, db=None)


def test_no_tool_accepts_a_scope_parameter():
    """The core security invariant, checked mechanically rather than by review."""
    for spec in SqlToolProvider().get_tools(CTX, db=None):
        props = set(spec["parameters"].get("properties", {}))
        assert not props & {"chair_id", "rep_id", "rep_code"}, spec["name"]


#: Tasks work with no Google account at all, so these are the tools a fresh
#: checkout and CI contribute.
TASK_TOOLS = {"list_tasks", "create_task", "update_task", "complete_task"}

#: Everything that needs a live connection. schedule_task is here rather than in
#: TASK_TOOLS because it writes to Google Calendar, even though its subject is a
#: task.
AGENDA_MAIL_TOOLS = {
    "agenda_status",
    "list_mail",
    "get_mail",
    "search_mail",
    "send_email",
    "list_calendar",
    "create_event",
    "update_event",
    "cancel_event",
    "schedule_task",
}


def _configured(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")
    monkeypatch.setattr(settings, "agenda_encryption_key", "0" * 43 + "=")


def test_the_agenda_provider_contributes_only_tasks_when_google_is_unconfigured():
    """The CI-green property, and the same discipline the MCP stub follows.

    A fresh checkout has no Google client, so no mail tool may appear. Tasks are
    local and always available, which is why they are separated out.
    """
    specs = AgendaToolProvider().get_tools(CTX, db=None)
    # `open_agenda` is the handoff and is present on every path (tasks live
    # behind it too); the mail tools are what must be absent with no Google.
    assert {s["name"] for s in specs} == TASK_TOOLS | {"open_agenda"}


def test_an_unconnected_rep_gets_one_tool_that_can_explain_itself(monkeypatch):
    """Configured but not connected.

    agenda_status exists so "check my mail" gets "your mailbox isn't connected —
    connect it in Settings" instead of the model claiming it has no such ability.
    """
    _configured(monkeypatch)
    specs = AgendaToolProvider().get_tools(CTX, db=None)
    assert {s["name"] for s in specs} == TASK_TOOLS | {"agenda_status", "open_agenda"}


def test_a_connected_rep_gets_the_full_agenda_tool_set(monkeypatch):
    _configured(monkeypatch)
    ctx = dataclasses.replace(CTX, email_account="rep@example.test")
    specs = AgendaToolProvider().get_tools(ctx, db=None)
    assert {s["name"] for s in specs} == TASK_TOOLS | AGENDA_MAIL_TOOLS | {"open_agenda"}

    # And the two sets agree with what the module publishes, so a tool added to
    # one place and not the other is a failure here rather than a graph that
    # binds a tool the agenda agent is never allowed to call.
    from app.tools.agenda_tools import AGENDA_TOOL_NAMES

    assert TASK_TOOLS | AGENDA_MAIL_TOOLS | {"open_agenda"} == set(AGENDA_TOOL_NAMES)


def test_the_handoff_tool_is_reachable_from_the_full_registry(monkeypatch):
    """The bug C1 fixed: `open_agenda` must appear in what registry.build()

    actually yields, not only in a spec list a test hand-assembles. Without it
    the orchestrator has no way to reach the agenda node and the entire mail /
    calendar / tasks experience is dead in the running app while every unit test
    stays green (see graph.py's HANDOFF_TOOL routing).
    """
    _configured(monkeypatch)
    ctx = dataclasses.replace(CTX, email_account="rep@example.test")
    names = {s["name"] for s in registry.build(ctx, db=None)}
    assert "open_agenda" in names
    # A representative gated agenda tool is built too, so the handoff leads
    # somewhere real.
    assert "send_email" in names


def test_the_handoff_is_present_even_with_only_task_tools():
    """Tasks live behind the same handoff, so it must exist with no Google."""
    names = {s["name"] for s in registry.build(CTX, db=None)}
    assert "open_agenda" in names
    assert "create_task" in names


def test_the_mcp_stub_is_still_the_empty_seam():
    """Kept strict, unlike the RAG and agenda assertions above.

    The agenda is its own provider, not an MCP server, so nothing has landed on
    the MCP seam and composing with it must still change nothing.
    """
    without = {s["name"] for s in ToolRegistry([SqlToolProvider()]).build(CTX, db=None)}
    with_mcp = {
        s["name"]
        for s in ToolRegistry([SqlToolProvider(), McpToolProvider()]).build(CTX, db=None)
    }
    assert with_mcp == without


def test_no_tool_accepts_a_mailbox_parameter(monkeypatch):
    """CLAUDE.md §1.7, made mechanical rather than reviewed.

    A tool that takes the account name is a tool the model can point at another
    inbox — and the text composing that argument may have arrived inside a mail
    body.
    """
    _configured(monkeypatch)
    ctx = dataclasses.replace(CTX, email_account="rep@example.test")
    for spec in registry.build(ctx, db=None):
        assert not forbidden_names_in(spec["parameters"]), spec["name"]


def test_the_registry_rejects_a_mailbox_parameter_nested_inside_an_array():
    """The check is recursive because `attendees` made nesting reachable.

    Looking only at top-level properties was sufficient while every parameter was
    a scalar; a forbidden name one level down would have passed silently.
    """

    class Rogue:
        name = "rogue"

        def get_tools(self, ctx, conn):
            return [
                {
                    "name": "invite",
                    "description": "",
                    "handler": None,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "attendees": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {"email_account": {"type": "string"}},
                                    "required": ["email_account"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["attendees"],
                        "additionalProperties": False,
                    },
                }
            ]

    with pytest.raises(ValueError, match="mailbox/account/sender"):
        ToolRegistry([Rogue()]).build(CTX, db=None)


def test_registry_rejects_a_provider_whose_tool_takes_chair_id():
    class Rogue:
        name = "rogue"

        def get_tools(self, ctx, conn):
            return [
                {
                    "name": "peek",
                    "description": "",
                    "parameters": {
                        "type": "object",
                        "properties": {"chair_id": {"type": "integer"}},
                        "required": ["chair_id"],
                        "additionalProperties": False,
                    },
                    "handler": None,
                }
            ]

    with pytest.raises(ValueError, match="chair_id/rep_id"):
        ToolRegistry([Rogue()]).build(CTX, db=None)


def test_every_spec_is_strict_mode_compatible():
    """OpenAI strict mode requires every property in `required` and no extras."""
    for spec in SqlToolProvider().get_tools(CTX, db=None):
        params = spec["parameters"]
        assert params.get("additionalProperties") is False, spec["name"]
        assert set(params.get("properties", {})) == set(params.get("required", [])), spec["name"]


class _CountingPool:
    """A fake pool that records checkout balance.

    The mechanism under test: providers receive the POOL and handlers check a
    connection out per call, releasing it before they return. A connection
    pinned for the whole turn is exactly what let ten concurrent chats exhaust
    a ten-connection pool while the database sat idle behind the model.
    """

    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = rows or []
        self.open = 0
        self.checkouts = 0

    def connection(self):
        import contextlib

        pool = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                return self

            def fetchall(self):
                return list(pool.rows)

        class _Conn:
            def cursor(self, **kwargs):
                return _Cursor()

            def execute(self, sql, params=None):
                return _Cursor()

        @contextlib.contextmanager
        def _checkout():
            pool.open += 1
            pool.checkouts += 1
            try:
                yield _Conn()
            finally:
                pool.open -= 1

        return _checkout()


async def test_sql_handlers_release_the_connection_before_returning():
    """No connection outlives a tool call — checkout is per call, not per turn."""
    pool = _CountingPool(rows=[])
    specs = {s["name"]: s["handler"] for s in SqlToolProvider().get_tools(CTX, pool)}

    result = await specs["get_doctor_chemists"](doctor_id=1)
    assert "chemists" in result
    assert pool.checkouts >= 1, "the handler never touched the pool it was given"
    assert pool.open == 0, "a connection was still checked out after the handler returned"
