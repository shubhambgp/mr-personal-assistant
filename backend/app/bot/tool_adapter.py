"""ToolSpec -> LangChain StructuredTool. The whole point is what it does NOT change.

The security model of this app is that a tool closes over a verified RepContext
and no tool's schema may accept a chair_id (CLAUDE.md §1.1-1.2). That invariant
is enforced mechanically by `ToolRegistry.build()`, which runs *before* this
module. Adapting at the boundary — rather than rewriting the ten SQL tools into
`@tool`-decorated functions — is deliberate: the invariant is the project's core
claim, and re-expressing a security claim inside someone else's abstraction is
exactly how such claims get quietly lost.

So after this module exists:
  * app/tools/sql_tools.py         unchanged
  * app/tools/base.py, registry.py unchanged
  * the RAG and MCP provider seams unchanged
  * tests/test_tool_registry.py    passes unmodified

Verified against langchain-core 1.6 (see ENGINEERING_LOG entry 15): StructuredTool
accepts our raw JSON Schema as `args_schema` directly, dispatches to the async
coroutine with the parsed arguments as kwargs, and `bind_tools(strict=True)`
reproduces the exact wire shape the previous hand-rolled `_to_openai_tools`
produced, including `strict: True`.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from ..tools.base import ToolSpec

#: Metadata key carrying the HITL approval flag through to the graph.
APPROVAL_KEY = "requires_approval"

#: Metadata key carrying the editable-argument whitelist through to the graph.
#: Sibling of APPROVAL_KEY: a gate is only useful if the human can act on what
#: they see, and an edit is only safe if the set of editable fields is decided by
#: the provider rather than by the UI submitting the edit.
EDITABLE_KEY = "approval_editable"


def _wrap(spec: ToolSpec):
    """Returns a coroutine that never raises.

    CLAUDE.md: "Errors reaching the model are returned as json.dumps({"error":
    ...}), never raised. A raised exception becomes an opaque tool failure; a
    returned error is something the model can read and explain to the rep."

    Catching here rather than letting LangGraph's ToolNode catch keeps that
    format ours. ToolNode's own error handling would produce a LangChain-shaped
    string the model has never been taught to read.
    """
    handler = spec["handler"]
    name = spec["name"]

    async def run(**kwargs: Any) -> str:
        try:
            return await handler(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surfaced to the model, by design
            # First line only, and the class name as the fallback. A raw
            # exception str can carry a DSN, host/port or user (e.g. a
            # psycopg.OperationalError), and this string is streamed to the
            # browser as tool_end.output — _explain only guards the `error`
            # event, not this one. Mirrors run_sql's own trimming. audit finding M-BE7.
            detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            return json.dumps({"error": f"{name} failed: {detail}"})

    run.__name__ = name
    return run


def to_langchain_tools(specs: list[ToolSpec]) -> list[StructuredTool]:
    """Adapt registry output for binding to a chat model.

    Call `ToolRegistry.build()` first — it is what rejects a tool declaring
    chair_id/rep_id/rep_code. This function assumes that check has passed and
    does not repeat it, because a second copy of a security check is a second
    thing that can drift.
    """
    return [
        StructuredTool(
            name=spec["name"],
            description=spec["description"],
            args_schema=spec["parameters"],
            coroutine=_wrap(spec),
            # Sync path deliberately absent: every handler here is async, and a
            # blocking fallback would silently stall the event loop.
            func=None,
            metadata={
                APPROVAL_KEY: bool(spec.get(APPROVAL_KEY, False)),
                EDITABLE_KEY: tuple(spec.get(EDITABLE_KEY, ())),
            },
        )
        for spec in specs
    ]


def requires_approval(tool: StructuredTool) -> bool:
    """Whether a call to this tool must be approved by a human first.

    Read by `route()` in app/bot/graph.py, which sends a gated round to the
    compliance reviewer and then to the approval interrupt. True for exactly the
    tools that write to Google — the five in agenda_tools.GATED_TOOL_NAMES,
    asserted in both directions by test_exactly_the_write_capable_tools_are_gated.
    """
    return bool((tool.metadata or {}).get(APPROVAL_KEY, False))


def editable_args(tool: StructuredTool) -> tuple[str, ...]:
    """Argument names a human may change at the approval gate.

    Content only — never a recipient, mailbox, thread or calendar id. Empty by
    default, so a newly gated tool is un-editable until its provider says
    otherwise; the failure mode of forgetting to populate this is a read-only
    card, not an editable recipient.

    Enforced in app/bot/graph.py's approval node, NOT here and NOT in the UI:
    the approval card renders this list, and the card is not trusted.
    """
    return tuple((tool.metadata or {}).get(EDITABLE_KEY, ()))
