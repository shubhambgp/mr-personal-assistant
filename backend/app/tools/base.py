"""The tool contract, and the registry that assembles tools from providers.

Why a registry rather than one build_tools() function: RAG retrieval and an
email MCP server are the next two milestones. `graph.run_turn` already takes an
opaque `list[ToolSpec]` and resolves handlers by name, so it needs no change to
gain a new tool family — but *something* has to compose the families, check for
name collisions, and guarantee every one of them is scoped to the same
RepContext. That is this module.

The rule that must not be broken (also in CLAUDE.md): a provider receives a
RepContext and closes over it. No tool's JSON Schema may accept a chair_id or
rep_id, because then the model could ask for someone else's data — nor a
mailbox/account/sender, because then it could send from, or read, someone else's
inbox. Both families are refused by name in `forbidden_names_in`, at any depth.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable

from ..bot.context import RepContext


class ToolSpec(TypedDict):
    """One tool, provider-agnostic (no dependency on any LLM SDK's shape).

    `parameters` must be a strict-mode-compatible JSON Schema: every property
    listed in `required`, and `additionalProperties: false`.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[str]]

    #: Human-in-the-loop gate. When True the graph pauses via interrupt() and
    #: waits for a person to approve the call before the handler runs. Set by
    #: every tool that writes to Google — the five in
    #: agenda_tools.GATED_TOOL_NAMES, always via _write_tool(). Read through
    #: app/bot/tool_adapter.py:requires_approval().
    requires_approval: NotRequired[bool]

    #: Argument names a human may edit at the approval gate, before approving.
    #:
    #: CONTENT ONLY. Never a recipient, mailbox, thread or calendar id: an
    #: editable recipient would turn the approval channel into an exfiltration
    #: channel — the model asks to send to Dr Sharma, the rep clicks approve,
    #: and a modified payload delivers somewhere else. Absent means nothing is
    #: editable, so a new gated tool is un-editable until someone deliberately
    #: says otherwise.
    #:
    #: The provider that owns the schema owns this policy; the approval card
    #: merely renders it, and the card is not trusted — app/bot/graph.py filters
    #: every submitted edit against this list server-side.
    #: Read through app/bot/tool_adapter.py:editable_args().
    approval_editable: NotRequired[tuple[str, ...]]


@runtime_checkable
class ToolProvider(Protocol):
    """A source of tools. Stateless; called once per turn.

    `db` is the READ-ONLY connection pool, not a checked-out connection. Handlers
    check a connection out per call and release it before returning — a
    connection held for the whole turn sat idle through the model's ~97.5% of
    the latency, so ten concurrent turns exhausted a ten-connection pool while
    the database did nothing.
    """

    name: str

    def get_tools(self, ctx: RepContext, db: Any) -> list[ToolSpec]: ...


class ToolRegistry:
    """Composes providers into the flat list the agent loop consumes."""

    def __init__(self, providers: list[ToolProvider]) -> None:
        self._providers = providers

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    def build(self, ctx: RepContext, db: Any) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        seen: dict[str, str] = {}  # tool name -> providing provider

        for provider in self._providers:
            for spec in provider.get_tools(ctx, db):
                name = spec["name"]
                if name in seen:
                    # Fail loudly. A silent shadow would mean the model calls a
                    # tool and a *different* implementation answers — e.g. a
                    # remote MCP server overriding our own get_daily_plan.
                    raise ValueError(
                        f"duplicate tool name {name!r}: provided by both "
                        f"{seen[name]!r} and {provider.name!r}. Tool names must "
                        f"be unique across providers."
                    )
                if forbidden := forbidden_names_in(spec.get("parameters") or {}):
                    raise ValueError(
                        f"tool {name!r} from provider {provider.name!r} declares "
                        f"{sorted(forbidden)}. Scope and mailbox come from RepContext "
                        f"by closure, never from the model — no chair_id/rep_id and no "
                        f"mailbox/account/sender parameter. See CLAUDE.md §1.2 and §1.7."
                    )
                seen[name] = provider.name
                specs.append(spec)
        return specs


#: Identity the model must never be able to name. Two families, one check.
#:
#: The scope family (chair_id/rep_id/rep_code) is the original invariant: a tool
#: that accepts it hands tenancy to the model.
#:
#: The mailbox family is the same mistake in a new shape. A Google connection is
#: a credential to a real person's inbox, so a parameter naming the account, the
#: sender or the from-address is a parameter through which model-composed text —
#: including text that arrived inside a mail body — could point the send path at
#: a different mailbox. The mailbox comes from RepContext by closure.
#:
#: `to` is deliberately NOT here: the recipient is what the human approves at the
#: gate, and it is controlled instead by deriving it from the thread server-side
#: plus a correspondent allowlist (see app/tools/agenda_tools.py).
#:
#: `from` is deliberately NOT here either — `from_date` is a legitimate parameter
#: name, and a guard that fires on correct code is a guard that gets removed.
_FORBIDDEN_PARAMS = {
    "chair_id",
    "rep_id",
    "rep_code",
    "mailbox",
    "email_account",
    "account",
    "from_address",
    "from_email",
    "sender",
}


def forbidden_names_in(schema: dict[str, Any]) -> set[str]:
    """Every forbidden identity parameter in a JSON Schema, at any depth.

    RECURSIVE, and it did not used to be. Looking only at top-level `properties`
    was sufficient while every tool parameter was a scalar. `create_event` takes
    an `attendees` array, so a nested object schema is now reachable — and a
    forbidden name one level down would have passed the old check silently.
    """
    found: set[str] = set()
    if not isinstance(schema, dict):
        return found

    # Recurse only into keys that are actually PRESENT and are dicts. Writing
    # `schema.get("items") or {}` instead looks like a safe default and is an
    # infinite regress: an absent key becomes {}, {} is a dict, and {} has an
    # absent key. Caught by test_duplicate_tool_name_is_rejected, which had
    # nothing to do with schemas — it just built a real tool list.
    props = schema.get("properties")
    if isinstance(props, dict):
        found |= _FORBIDDEN_PARAMS & set(props)
        for sub in props.values():
            found |= forbidden_names_in(sub)

    items = schema.get("items")
    if isinstance(items, dict):
        found |= forbidden_names_in(items)

    for key in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for sub in branches:
                found |= forbidden_names_in(sub)
    return found


