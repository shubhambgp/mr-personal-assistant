"""The agent as a LangGraph StateGraph.

WHY THIS EXISTS AT ALL. The hand-rolled loop in agent.py works and is smaller.
The reason to replace it is human-in-the-loop: `interrupt()` plus a checkpointer
lets a turn pause, wait for a person, and resume — and an approval gate ("send
this to Dr Sharma?") is a real compliance requirement for a field-force tool.
That is the one thing genuinely painful to hand-roll. Nothing else here is an
improvement for its own sake.

    START -> agent --+-- no calls ------------------------------> END
                     +-- over budget --> cap ------------------> END
                     +-- calls -> tools --+-- handoff --> agenda
                                          +-- else -----> agent

    agenda --+-- no calls -----------------------------------> END
             +-- ungated calls --> tools --> agenda
             +-- gated call ----> review --> approval[interrupt] --+- ok -> tools
                                                                   +- no -> agenda

THREE AGENTS, THREE NODES, ONE GRAPH. What distinguishes an agent here is its
instructions, the tools it may call, and whether its output is reviewed — all
three are per-node. What is shared is the message channel, and that is a feature:
when the rep says "email the doctor you just briefed me on", the agenda agent
needs the orchestrator's get_doctor_brief result in its context.

Nested subgraphs were measured and rejected (ENGINEERING_LOG 20). Three findings
against them, in the installed langgraph 1.2.11: a parent-level Command(resume=)
CANNOT rewrite a subgraph's pending tool args — the edit is silently discarded
and the ORIGINAL draft would be sent; a subgraph's token deltas never reach the
stream reader without `subgraphs=True`; and `subgraphs=True` changes the yielded
tuple's arity, breaking every reader branch. Agent-as-tool was rejected too:
`tool_adapter._wrap` catches Exception, and GraphInterrupt IS an Exception, so
the pause would be swallowed into `{"error": ...}`.

`route` deliberately sends a gated round to `review` and never straight to
`approval`, so "the reviewer's verdict exists before the human sees the card" is
a property of the graph rather than a convention someone can forget.

`run_turn` mirrors `agent.run_turn`'s signature and returns the same
`TurnResult`, so the API layer and the eval harness use one call site.
Guardrails stay in the API layer where they already live.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from ..tools.base import ToolSpec
from .agent import MAX_TOOL_ROUNDS, ToolTrace, TurnResult, build_instructions
from .context import RepContext
from .tool_adapter import editable_args, requires_approval, to_langchain_tools

OnTextDelta = Any
OnToolStart = Any
OnToolEnd = Any

#: langgraph's own key for interrupts on the `updates` stream. Spelled out here
#: rather than imported from a private module because it is a wire contract: the
#: SSE layer downstream depends on this branch firing.
INTERRUPT_KEY = "__interrupt__"

#: Nodes whose token deltas are prose meant for the rep. An ALLOWLIST, not a
#: `!= "agent"` test, for two reasons: the agenda agent's tokens must reach the
#: stream (with the old filter it answered in total silence), and the compliance
#: reviewer's must NOT — it is an internal reviewer, and its verdict reaches the
#: rep as structured data in the approval card instead.
TEXT_NODES = frozenset({"agent", "agenda"})

#: The orchestrator's handoff tool. Bound to `agent` even though it is named in
#: the agenda tool set, because the orchestrator is the one that calls it.
HANDOFF_TOOL = "open_agenda"

#: Read-only tools the agenda agent needs IN ADDITION to its own set, without
#: taking them away from the orchestrator. Retrieval is the one that matters:
#: AGENDA_RULES requires every clinical claim in a draft to trace to
#: search_literature results retrieved in THIS turn, and the orchestrator hands
#: off before it retrieves — so if the agenda agent cannot retrieve either, the
#: compliant clinical-email flow can only ever refuse. These stay in `core` too
#: (they are not in the agenda-only set), so binding them here gives them to both
#: agents rather than moving them. Both are read-only and tenancy-scoped, so this
#: is safe. See audit finding H6.
AGENDA_SHARED_TOOLS = frozenset({"search_literature", "list_documents"})


class AgentState(TypedDict):
    """Checkpointed state — deliberately minimal.

    `RepContext` is NOT here. State is persisted and resumable; identity must be
    re-derived from the verified JWT on every entry, including a HITL resume.
    Putting it in state would mean a resumed thread could carry a stale or
    foreign identity. It travels in `config["configurable"]` instead, and tools
    close over it exactly as they did before.

    Text, tool results and timings are not here either: the runner below
    accumulates them from the event stream, so nothing that is merely *observed*
    becomes persisted state that could drift from the messages.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    rounds: int

    #: The compliance reviewer's verdict on the pending outbound draft.
    #:
    #: This is here, and nothing else is, because it is the one value PRODUCED
    #: by one node and CONSUMED by another ACROSS the interrupt boundary. A node
    #: re-executes from its start on resume (verified against langgraph 1.2.11),
    #: so `approval` must READ the verdict rather than recompute it — a second
    #: model call could return a different verdict from the one the rep actually
    #: saw and approved, and then the audit record would describe a review that
    #: never happened.
    #:
    #: It is not "merely observed" in the sense the docstring above forbids: it
    #: cannot be re-derived from the messages.
    review: dict | None


def _pending_tool_calls(state: AgentState) -> list[dict]:
    last = state["messages"][-1] if state["messages"] else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return list(last.tool_calls)
    return []


def _literature_in(state: AgentState) -> list[dict]:
    """The {document, section, text} passages retrieved earlier in this turn.

    Mined back out of the transcript rather than threaded through the graph, the
    same way chat.py's `_retrieved_passages` reads them out of the event stream.
    The reviewer needs to know what the approved literature actually said; the
    graph core stays unaware that retrieval exists.
    """
    passages: list[dict] = []
    for message in state["messages"]:
        if not isinstance(message, ToolMessage) or message.name != "search_literature":
            continue
        raw = message.content if isinstance(message.content, str) else str(message.content)
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for row in payload.get("rows") or []:
            if isinstance(row, dict):
                passages.append(
                    {
                        "document": row.get("document"),
                        "section": row.get("section"),
                        "text": row.get("content") or row.get("text"),
                    }
                )
    return passages


def _apply_edits(
    ai: AIMessage, gated_ids: set[str], edits: dict, by_name: dict
) -> AIMessage | None:
    """Rewrites a pending call's arguments in place. Returns None if nothing changed.

    THE SECURITY RULE, and this is the only place it is enforced: an edit may
    change what is SAID and never who it is said TO. The whitelist comes from the
    tool's own metadata, so the provider that owns the schema owns the policy —
    the approval card merely renders it, and the card is not trusted. `to`,
    `thread_id`, `attendees` and anything the model never declared are absent
    from every whitelist and are therefore dropped, silently and by default.
    """
    changed = False
    rewritten: list[dict] = []
    for call in ai.tool_calls or []:
        patch = edits.get(call["id"]) or {}
        tool = by_name.get(call["name"])
        if call["id"] in gated_ids and patch and tool is not None:
            allowed = set(editable_args(tool))
            keep = {
                key: value
                for key, value in patch.items()
                if key in allowed and isinstance(value, str)
            }
            if keep and any(call["args"].get(k) != v for k, v in keep.items()):
                rewritten.append({**call, "args": {**call["args"], **keep}})
                changed = True
                continue
        rewritten.append(call)

    if not changed:
        return None

    # The provider's RAW copy of the arguments is stripped deliberately.
    # langchain-openai builds the Responses payload from `content` /
    # `additional_kwargs` first and only adds a `function_call` entry "if not
    # already present" — so an untouched raw copy would WIN, and the original
    # text would be sent while the card showed the rep's edit. Defended rather
    # than assumed: this is the one thing the spike could not verify offline.
    kwargs = {k: v for k, v in (ai.additional_kwargs or {}).items() if k != "tool_calls"}
    content = ai.content
    if isinstance(content, list):
        content = [
            block
            for block in content
            if not (isinstance(block, dict) and block.get("type") in {"function_call", "tool_call"})
        ]
    return ai.model_copy(
        update={"tool_calls": rewritten, "additional_kwargs": kwargs, "content": content}
    )


def build_graph(
    *,
    llm: ChatOpenAI,
    tool_specs: list[ToolSpec],
    instructions: str,
    cache_key: str,
    agenda_instructions: str = "",
    agenda_tools: frozenset[str] = frozenset(),
    reviewer: Any = None,
):
    """Compiles the graph for one rep's turn.

    Built per turn rather than once at import: `instructions` carries the data
    vintage and `cache_key` partitions the prompt cache per rep. Compilation is
    cheap; getting either of those wrong is not.

    The last three arguments are optional with inert defaults, so every existing
    caller and every existing test keeps working unchanged: with no agenda tools
    the graph is exactly the two-node shape it was.

    `llm` and `reviewer` are parameters rather than module lookups for the same
    reason — it is what lets the tests drive the real topology with a scripted
    fake and no API call.
    """
    tools = to_langchain_tools(tool_specs)
    by_name = {t.name: t for t in tools}

    # The personality partition lives HERE and nowhere else. `tools` stays a
    # single ToolNode over everything, because ToolNode only dispatches by name
    # and a model cannot call a tool it was never given. So authority is decided
    # by what each agent binds, not by which node executes.
    core = [t for t in tools if t.name not in agenda_tools or t.name == HANDOFF_TOOL]
    agenda_bound = [
        t
        for t in tools
        if (t.name in agenda_tools or t.name in AGENDA_SHARED_TOOLS)
        and t.name != HANDOFF_TOOL
    ]

    model = llm.bind_tools(core, strict=True)
    agenda_model = llm.bind_tools(agenda_bound, strict=True) if agenda_bound else model

    async def agent(state: AgentState) -> dict:
        # The system message is prepended here rather than stored in state, so
        # it is never duplicated as the thread grows and always reflects the
        # current data vintage.
        messages = [SystemMessage(instructions), *state["messages"]]
        reply = await model.ainvoke(messages, prompt_cache_key=cache_key)
        return {"messages": [reply], "rounds": state.get("rounds", 0) + 1}

    async def agenda(state: AgentState) -> dict:
        """The mail + calendar + tasks agent.

        A near-copy of `agent` with different instructions and a different tool
        binding, which is precisely what makes it a different agent. Same
        transcript, so it can act on what the orchestrator already looked up.
        """
        messages = [SystemMessage(agenda_instructions or instructions), *state["messages"]]
        reply = await agenda_model.ainvoke(messages, prompt_cache_key=cache_key)
        return {"messages": [reply], "rounds": state.get("rounds", 0) + 1}

    async def review(state: AgentState) -> dict:
        """Reviews an outbound draft before any human is asked to approve it.

        A separate agent because the rules differ. Text on the rep's SCREEN may
        be internal and approximate; text the rep SENDS to a prescriber is
        promotional material and must be traceable to the approved literature.

        Its own node rather than a step inside `approval`, because `approval`
        re-executes on resume — a reviewer in there would run twice and could
        contradict the verdict the rep approved.
        """
        gated = _gated(state)
        if not gated or reviewer is None:
            return {"review": None}
        verdict = await reviewer(calls=gated, passages=_literature_in(state))
        return {"review": verdict if isinstance(verdict, dict) else None}

    async def approval(state: AgentState) -> dict:
        """Pauses the turn until a human decides.

        Reached only through `review`, so `state["review"]` is always populated
        by the time the rep sees the card.
        """
        calls = _pending_tool_calls(state)
        gated = _gated(state)
        decision = interrupt(
            {
                "reason": "approval_required",
                "review": state.get("review"),
                "calls": [
                    {
                        "id": c["id"],
                        "name": c["name"],
                        "args": c["args"],
                        # Policy travels with the payload so the card renders it
                        # rather than inventing it. Enforced again on the way
                        # back, because the card is not trusted.
                        "editable": list(editable_args(by_name[c["name"]])),
                    }
                    for c in gated
                ],
            }
        )
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        edits = (decision.get("edits") or {}) if isinstance(decision, dict) else {}
        gated_ids = {c["id"] for c in gated}

        if not approved:
            # Every call must still be answered: OpenAI rejects a follow-up turn
            # whose history contains a tool call with no output, which is why
            # `cap` exists too. But the content has to be TRUE per call. This
            # used to say "the rep declined" for every pending call in the
            # round, including read-only ones the rep was never shown — so the
            # model would explain a refusal that never happened.
            return {
                "messages": [
                    ToolMessage(
                        content=(
                            '{"error": "The rep declined to approve this action."}'
                            if c["id"] in gated_ids
                            else '{"error": "Not run: it shared a round with an action '
                            'the rep declined. Ask again if it is still needed."}'
                        ),
                        tool_call_id=c["id"],
                        name=c["name"],
                    )
                    for c in calls
                ]
            }

        pending_ai = state["messages"][-1] if state["messages"] else None
        if edits and isinstance(pending_ai, AIMessage):
            merged = _apply_edits(pending_ai, gated_ids, edits, by_name)
            if merged is not None:
                # Same id, so add_messages REPLACES rather than appends and the
                # transcript order survives. ToolNode then dispatches the edited
                # arguments (verified against langgraph 1.2.11).
                return {"messages": [merged]}
        return {}

    async def cap(state: AgentState) -> dict:
        """The tool-round backstop, reported rather than silently truncated.

        Unresolved tool calls must still be answered: OpenAI rejects a follow-up
        turn whose history contains a tool call with no output, so leaving them
        dangling would break the *next* message in this thread. The old loop did
        not have to care, because `previous_response_id` meant it never re-sent
        the history.
        """
        return {
            "messages": [
                ToolMessage(
                    content='{"error": "Tool budget for this turn exhausted."}',
                    tool_call_id=c["id"],
                    name=c["name"],
                )
                for c in _pending_tool_calls(state)
            ]
        }

    def _gated(state: AgentState) -> list[dict]:
        """Pending calls that need a human. `by_name.get`, never `by_name[...]`.

        A hallucinated tool name used to raise KeyError here — an unhandled
        exception inside a node, which surfaces to the rep as the generic
        "something went wrong" that CLAUDE.md §4 exists to prevent. `route` had
        the guard; these nodes did not.
        """
        return [
            c
            for c in _pending_tool_calls(state)
            if (tool := by_name.get(c["name"])) is not None and requires_approval(tool)
        ]

    def _caller(state: AgentState) -> str:
        """Which agent asked for the tools that just ran.

        Derived from the transcript rather than stored in state: the last
        AIMessage carrying tool calls IS the caller, so nothing merely observed
        becomes persisted state that could drift from the messages.
        """
        for message in reversed(state["messages"]):
            if isinstance(message, AIMessage) and message.tool_calls:
                names = {c["name"] for c in message.tool_calls}
                if HANDOFF_TOOL in names:
                    # The handoff itself: control moves TO the agenda agent.
                    return "agenda"
                if names & agenda_tools:
                    return "agenda"
                return "agent"
        return "agent"

    def route(state: AgentState) -> str:
        calls = _pending_tool_calls(state)
        if not calls:
            return END
        if state.get("rounds", 0) >= MAX_TOOL_ROUNDS:
            return "cap"
        if _gated(state):
            # Never straight to `approval`: the reviewer runs first, by
            # construction rather than by convention.
            return "review"
        return "tools"

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent)
    builder.add_node("agenda", agenda)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("review", review)
    builder.add_node("approval", approval)
    builder.add_node("cap", cap)

    builder.add_edge(START, "agent")
    for node in ("agent", "agenda"):
        builder.add_conditional_edges(
            node,
            route,
            {END: END, "tools": "tools", "review": "review", "cap": "cap"},
        )
    # Back to whichever agent asked. This edge is the whole handoff mechanism:
    # `open_agenda` is an ordinary ungated tool, so ToolNode answers it (leaving
    # no unanswered call in the transcript) and the routing happens here.
    builder.add_conditional_edges("tools", _caller, {"agent": "agent", "agenda": "agenda"})
    builder.add_edge("review", "approval")
    # After approval either the tools run (approved, possibly with edits) or
    # refusal messages are already in state and the caller explains them.
    builder.add_conditional_edges(
        "approval",
        lambda st: "tools" if _pending_tool_calls(st) else _caller(st),
        {"tools": "tools", "agent": "agent", "agenda": "agenda"},
    )
    builder.add_edge("cap", END)
    return builder


def build_user_message(user_message: str, images: list[dict] | None = None) -> HumanMessage:
    """One user turn, text or text+images.

    Uses LangChain's standard image block rather than OpenAI's `input_image`
    shape. All three were verified to work (ENGINEERING_LOG 15); the standard
    one is the portable choice, which is the point of having moved history off
    `previous_response_id`.
    """
    if not images:
        return HumanMessage(user_message)

    content: list[dict] = [
        {"type": "text", "text": user_message or "Please analyse this image."}
    ]
    for image in images:
        data_url: str = image["data_url"]
        header, _, b64 = data_url.partition(",")
        mime = header.removeprefix("data:").removesuffix(";base64") or "image/png"
        content.append(
            {"type": "image", "source_type": "base64", "data": b64, "mime_type": mime}
        )
    return HumanMessage(content=content)


async def _drive(
    *,
    entry: Any,
    ctx: RepContext,
    tool_specs: list[ToolSpec],
    thread_id: str,
    vintage_summary: str,
    on_text_delta: OnTextDelta,
    on_tool_start: OnToolStart,
    on_tool_end: OnToolEnd,
    checkpointer: Any = None,
    model: str | None = None,
    llm: Any = None,
    agenda_instructions: str = "",
    agenda_tools: frozenset[str] = frozenset(),
    reviewer: Any = None,
) -> TurnResult:
    """The stream reader. `entry` is a fresh state dict, or a Command to resume.

    EXACTLY ONE place unpacks `astream`, so a change in the yielded shape cannot
    be fixed on the first leg and forgotten on the resume leg. `run_turn` and
    `resume_turn` are both thin wrappers around this.

    Streaming is read from `astream` with the documented `messages` and `updates`
    modes rather than `astream_events`, on purpose: `updates` hands us the real
    `AIMessage` and `ToolMessage` objects, so tool-call ids come from public
    message fields instead of being mined out of event internals.
    """
    from ..config import settings
    from .agent import DEFAULT_MODEL  # local: avoids a cycle at import time

    if llm is None:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set — the chat endpoint cannot run without it."
            )
        llm = ChatOpenAI(
            model=model or DEFAULT_MODEL,
            # Passed explicitly from settings, NOT left to ChatOpenAI's environment
            # lookup. pydantic-settings reads .env into `settings` without exporting
            # to os.environ, so the env fallback finds nothing under uvicorn. The
            # eval harness happened to work because it calls load_dotenv() itself —
            # which is exactly why the HTTP path needed testing separately.
            api_key=settings.openai_api_key,
            use_responses_api=True,
            reasoning={"effort": "medium"},
            # Pinned rather than left to the default (currently None, i.e. classic
            # string content). langchain-openai only branches on "v1", so this is a
            # no-op today — but it stops a future default flip to content-block
            # output from silently changing what the streaming reader below sees.
            output_version="v0",
        )
    # NOTE on prompt_cache_key: it is passed at *invoke* time in the agent node,
    # not here, because it varies per rep. Verified in ENGINEERING_LOG 15 to
    # reach the request payload. It partitions OpenAI's prompt cache per rep so
    # one rep's cached prefix can never be served to another — a security
    # property, not a speed tweak.

    graph = build_graph(
        llm=llm,
        tool_specs=tool_specs,
        instructions=build_instructions(ctx, vintage_summary),
        cache_key=ctx.cache_key(),
        agenda_instructions=agenda_instructions,
        agenda_tools=agenda_tools,
        reviewer=reviewer,
    ).compile(checkpointer=checkpointer)

    config = {
        "configurable": {
            # The conversation uuid, which conversations.get_or_create has
            # already proven this rep owns. Never client-supplied directly.
            "thread_id": thread_id,
            # Identity travels here, not in checkpointed state.
            "rep": ctx,
        },
        # Four supersteps per round plus slack, because an agenda turn spends
        # them on handoff, review and approval as well as agent/tools. Too low
        # and a legitimate turn dies as GraphRecursionError, which reaches the
        # rep as a generic model failure.
        "recursion_limit": MAX_TOOL_ROUNDS * 4 + 8,
    }

    text_parts: list[str] = []
    tool_results_parts: list[str] = []
    trace: list[ToolTrace] = []
    started_at: dict[str, float] = {}
    call_meta: dict[str, tuple[str, dict]] = {}
    input_tok = output_tok = cached_tok = 0
    hit_cap = False
    need_break = False
    pending: dict | None = None
    interrupt_id: str | None = None

    stream = graph.astream(entry, config=config, stream_mode=["messages", "updates"])

    async for mode, payload in stream:
        if mode == "messages":
            chunk, meta = payload
            if meta.get("langgraph_node") not in TEXT_NODES:
                continue
            text = chunk.text if isinstance(chunk.text, str) else ""
            if not text:
                continue
            if need_break and text_parts:
                # The model often narrates ("let me look that up"), calls a
                # tool, then writes the real answer. Without this the two run
                # together mid-sentence.
                need_break = False
                text_parts.append("\n\n")
                await on_text_delta("\n\n")
            need_break = False
            text_parts.append(text)
            await on_text_delta(text)
            continue

        # mode == "updates": {node_name: state_delta}
        for node, delta in (payload or {}).items():
            if node == INTERRUPT_KEY:
                # Verified shape: {"__interrupt__": (Interrupt(value=..., id=...),)}.
                # `delta` is a TUPLE, not a state dict — the loop below would
                # call .get() on it and raise AttributeError, which escaped as a
                # generic error AND left the thread wedged at a pending
                # interrupt with no way to resume. This branch must stay first.
                if delta:
                    pending = dict(delta[0].value or {})
                    interrupt_id = delta[0].id
                continue
            if node == "cap":
                hit_cap = True
            for message in (delta or {}).get("messages", []) or []:
                if isinstance(message, AIMessage):
                    usage = message.usage_metadata or {}
                    input_tok += usage.get("input_tokens", 0) or 0
                    output_tok += usage.get("output_tokens", 0) or 0
                    cached_tok += (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
                    for call in message.tool_calls or []:
                        call_id = call["id"]
                        if call_id in call_meta:
                            # An approved edit re-emits the same AIMessage with
                            # the same call ids. Reporting it again would double
                            # the timeline row and restart the timer.
                            continue
                        started_at[call_id] = time.perf_counter()
                        call_meta[call_id] = (call["name"], call["args"])
                        # Fires BEFORE the handler runs, which is what gives the
                        # UI a real in-flight state and a live timer.
                        await on_tool_start(call_id, call["name"], call["args"])
                elif isinstance(message, ToolMessage):
                    call_id = message.tool_call_id
                    name, args = call_meta.get(call_id, (message.name or "tool", {}))
                    duration_ms = (time.perf_counter() - started_at.get(call_id, time.perf_counter())) * 1000
                    result = message.content if isinstance(message.content, str) else str(message.content)
                    # A handler failure is already `{"error": ...}` JSON by the
                    # time it gets here (tool_adapter wraps it), so the error
                    # flag is derived from the payload rather than tracked
                    # separately. The UI already treated a soft `{"error"}` as a
                    # failure, so this is one consistent signal instead of two.
                    is_error = result.lstrip().startswith('{"error"')
                    trace.append(ToolTrace(name, duration_ms, is_error))
                    tool_results_parts.append(result)
                    await on_tool_end(call_id, name, args, result, is_error, duration_ms)
                    need_break = True

    paused = None
    if pending is not None:
        paused = {
            "reason": pending.get("reason", "approval_required"),
            "interrupt_id": interrupt_id,
            "calls": pending.get("calls") or [],
            "review": pending.get("review"),
        }

    return TurnResult(
        # Continuity is the checkpointer's thread now, not an OpenAI handle.
        response_id=None,
        final_text="".join(text_parts),
        tool_results_text="\n".join(tool_results_parts),
        input_tokens=input_tok,
        output_tokens=output_tok,
        cached_tokens=cached_tok,
        tool_trace=trace,
        hit_round_cap=hit_cap,
        interrupt=paused,
    )


async def run_turn(
    *,
    ctx: RepContext,
    tool_specs: list[ToolSpec],
    user_message: str,
    thread_id: str,
    vintage_summary: str,
    on_text_delta: OnTextDelta,
    on_tool_start: OnToolStart,
    on_tool_end: OnToolEnd,
    images: list[dict] | None = None,
    checkpointer: Any = None,
    model: str | None = None,
    llm: Any = None,
    agenda_instructions: str = "",
    agenda_tools: frozenset[str] = frozenset(),
    reviewer: Any = None,
) -> TurnResult:
    """One full turn. Mirrors `agent.run_turn`'s contract so the API layer and
    the eval harness can switch engines at a single call site.

    `llm` is accepted so a test can drive the real transport with a scripted
    fake. `build_graph` always took it; `run_turn` did not, which is why the
    streaming and interrupt plumbing was never testable without spending money.
    """
    return await _drive(
        entry={"messages": [build_user_message(user_message, images)], "rounds": 0},
        ctx=ctx,
        tool_specs=tool_specs,
        thread_id=thread_id,
        vintage_summary=vintage_summary,
        on_text_delta=on_text_delta,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
        checkpointer=checkpointer,
        model=model,
        llm=llm,
        agenda_instructions=agenda_instructions,
        agenda_tools=agenda_tools,
        reviewer=reviewer,
    )


async def resume_turn(
    *,
    ctx: RepContext,
    tool_specs: list[ToolSpec],
    decision: dict,
    thread_id: str,
    vintage_summary: str,
    on_text_delta: OnTextDelta,
    on_tool_start: OnToolStart,
    on_tool_end: OnToolEnd,
    checkpointer: Any = None,
    model: str | None = None,
    llm: Any = None,
    agenda_instructions: str = "",
    agenda_tools: frozenset[str] = frozenset(),
    reviewer: Any = None,
) -> TurnResult:
    """Continues a turn that paused for a human decision.

    `decision` is {"approved": bool, "edits": {call_id: {field: value}}}. It has
    already been through the API layer's ownership and interrupt-id checks; the
    editable-field whitelist is applied here, inside the graph, because that is
    the one place both entry points must pass through.

    `thread_id` is still not client-supplied in any meaningful sense: it only
    becomes a thread id after the endpoint has matched it against chair_id.
    """
    return await _drive(
        entry=Command(resume=decision),
        ctx=ctx,
        tool_specs=tool_specs,
        thread_id=thread_id,
        vintage_summary=vintage_summary,
        on_text_delta=on_text_delta,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
        checkpointer=checkpointer,
        model=model,
        llm=llm,
        agenda_instructions=agenda_instructions,
        agenda_tools=agenda_tools,
        reviewer=reviewer,
    )
