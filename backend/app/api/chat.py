"""The streaming chat endpoint.

Transport: Server-Sent Events over a POST. EventSource cannot POST, so the
client reads the body with fetch() + a ReadableStream reader — see
frontend/src/hooks/useChatStream.ts. SSE over POST is the right trade here: it
is one-directional, it survives proxies, and it needs no WebSocket lifecycle.

Event contract (one JSON object per `data:` line):

    {"type":"start",     "conversation_id": "..."}
    {"type":"tool_start","call_id":"...","name":"...","input":{...}}
    {"type":"tool_end",  "call_id":"...","name":"...","output":"...",
                         "is_error":false,"duration_ms":12.3}
    {"type":"token",     "delta":"..."}
    {"type":"grounding", "grounded":false,"unverified_claims":["5560"]}
    {"type":"notice",    "message":"..."}          # skipped attachments, caps
    {"type":"done",      "response_id":"...","usage":{...},"timing":{...}}
    {"type":"error",     "message":"..."}
    {"type":"approval_required",
                         "interrupt_id":"...","calls":[{...}],"review":{...}}

`tool_start` firing before the handler runs is the point: the previous UI only
learned about a tool call after it completed, so cards rendered with ~0s
duration and nothing appeared while a slow query was in flight.

`approval_required` is TERMINAL for its leg: the stream ends there and no `done`
is sent, because the turn has not produced an answer. The rep decides, the client
POSTs /api/chat/resume, and the continuation streams into the same message. There
is no `paused` event to invent — useChatStream already stops streaming when the
read loop ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..bot import agent, attachments, audit, compliance, db, graph, guardrails, schema
from ..bot.checkpointer import checkpointer
from ..bot.context import RepContext
from ..core.metrics import metrics
from ..core.security import RateLimiter
from ..deps import AgendaRep
from ..registry import registry
from ..services import conversations as convo_service
from ..tools.agenda_tools import AGENDA_TOOL_NAMES

router = APIRouter(prefix="/api/chat", tags=["chat"])
log = logging.getLogger(__name__)

_DONE = object()  # queue sentinel

#: Per-rep cost control on the two endpoints that invoke the model. 30 turns in
#: 10 minutes is far above any human chat cadence (a turn takes 10-60s), so a
#: legitimate rep never sees this — a scripted session driving unbounded LLM and
#: Gmail spend does (audit finding M-SEC5). In-process like the login limiter, with
#: the same single-worker caveat.
_turn_limiter = RateLimiter(max_attempts=30, window_seconds=600)


def _throttle_turn(rep: RepContext) -> None:
    key = str(rep.chair_id)
    if not _turn_limiter.check(key):
        metrics.incr("chat_rate_limited")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests in a short time. Wait a moment and try again.",
            headers={"Retry-After": str(_turn_limiter.retry_after(key))},
        )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


#: Kept in one place so the SSE headers cannot drift between the two endpoints.
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx must not buffer a stream
}


@router.post("/stream")
async def stream_chat(
    request: Request,
    rep: AgendaRep,
    message: Annotated[str, Form()] = "",
    conversation_id: Annotated[str | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
    # Filenames the client has JUST ingested through POST /api/documents. Not a
    # capability and not trusted: the documents are already in the rep's own
    # scope, retrieval is filtered by chair_id regardless, and this only adds a
    # visible line to the rep's own message so the model knows a file arrived.
    # Without it the rep attaches a PDF, asks "what is in this?", and the model
    # answers "I don't see a PDF" — which was true, because nothing told it.
    document_names: Annotated[list[str] | None, Form()] = None,
) -> StreamingResponse:
    _throttle_turn(rep)

    # The caps are enforced WHILE reading, not after. The previous shape read
    # every part fully into RAM and only then let collect_images apply the
    # count/size limits — so 500 x 14 MB parts were a worker OOM any
    # authenticated caller could drive (audit finding M-SEC4). Parts beyond the
    # count cap are never read at all; each read is bounded to one byte over
    # the size cap, which is exactly enough for collect_images to reject it
    # with its normal "too large" reason.
    uploads: list[tuple[str, str | None, bytes]] = []
    overflow = 0
    for upload in images or []:
        if len(uploads) >= attachments.MAX_IMAGES_PER_TURN:
            overflow += 1
            continue
        data = await upload.read(attachments.MAX_IMAGE_BYTES + 1)
        uploads.append((upload.filename or "attachment", upload.content_type, data))
    collected, skipped = attachments.collect_images(uploads)
    if overflow:
        skipped.append(
            f"{overflow} more file(s): over the "
            f"{attachments.MAX_IMAGES_PER_TURN}-image limit for one message"
        )

    if not message.strip() and not collected and not document_names:
        async def empty() -> AsyncIterator[str]:
            yield _sse({"type": "error", "message": "Send a message or attach an image."})
        return StreamingResponse(empty(), media_type="text/event-stream")

    # Appended to the rep's own text rather than smuggled into the system prompt:
    # it is visible in the transcript, persists with the turn, and so a reloaded
    # conversation still explains why the assistant read a document. The names
    # are truncated and newline-stripped so a crafted filename cannot forge
    # extra lines of "context".
    if document_names:
        named = ", ".join(n.replace("\n", " ").strip()[:120] for n in document_names[:5] if n.strip())
        if named:
            message = (
                f"{message}\n\n(Just added to my library: {named} — "
                f"use read_document to read it.)"
            ).strip()

    generator = _run(
        request=request,
        rep=rep,
        message=message,
        conversation_id=conversation_id,
        images=collected,
        skipped=skipped,
    )
    return StreamingResponse(generator, media_type="text/event-stream", headers=_SSE_HEADERS)


class ResumeRequest(BaseModel):
    """A human's decision on a paused turn.

    No thread_id, no chair_id, no tool name, no recipient and no mailbox — only
    the conversation, which is checked against this rep, the interrupt being
    answered, and content edits, which are filtered again inside the graph
    against each tool's own whitelist.
    """

    conversation_id: str
    interrupt_id: str
    approved: bool
    edits: dict[str, dict[str, str]] = Field(default_factory=dict)


@router.post("/resume")
async def resume_chat(
    request: Request,
    rep: AgendaRep,
    body: ResumeRequest,
) -> StreamingResponse:
    """Continue a turn that paused for approval.

    A resume is a SECOND entry point into the same checkpointed state, so it gets
    the same identity check as the first (CLAUDE.md §1.8): `AgendaRep` re-derives
    the rep from the verified JWT on this request, and ownership is re-checked
    below.

    Ownership uses the strict `owned_by`, NOT `get_or_create`. get_or_create
    deliberately creates a fresh conversation when the id is not the caller's —
    right for a new message mid-stream, and wrong here, where it would silently
    fork an empty thread and swallow the resume rather than refusing it.

    404 rather than 403, so "does not exist" and "is not yours" are
    indistinguishable from outside.
    """
    _throttle_turn(rep)

    if not await asyncio.to_thread(convo_service.owned_by, rep, body.conversation_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    # Atomically claim the card for THIS resume. A stale card, a replayed
    # request, a second tab, or an already-in-flight resume all return None here
    # — one statement does the ownership check, the interrupt-id match and the
    # claim, so two concurrent resumes cannot both proceed and double-send.
    pending = await asyncio.to_thread(
        convo_service.claim_pending_approval,
        rep,
        body.conversation_id,
        body.interrupt_id,
    )
    if pending is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That approval is no longer the current one, or is already being "
            "processed. Reload the conversation.",
        )

    generator = _run(
        request=request,
        rep=rep,
        message="",
        conversation_id=body.conversation_id,
        images=[],
        skipped=[],
        decision={"approved": body.approved, "edits": body.edits},
        drafted=pending,
    )
    return StreamingResponse(generator, media_type="text/event-stream", headers=_SSE_HEADERS)


async def _run(
    *,
    request: Request,
    rep: RepContext,
    message: str,
    conversation_id: str | None,
    images: list[dict],
    skipped: list[str],
    decision: dict | None = None,
    drafted: dict | None = None,
) -> AsyncIterator[str]:
    """One SSE leg. Drives a fresh turn, or resumes a paused one.

    Both legs share this generator on purpose: the queue, the client-disconnect
    cancellation, the guardrails, the persistence and the audit record are
    written once. `decision` is what makes it a resume.
    """
    resuming = decision is not None
    started = time.perf_counter()
    queue: asyncio.Queue = asyncio.Queue()

    if resuming:
        convo = {"id": conversation_id}
    else:
        convo = await asyncio.to_thread(
            convo_service.get_or_create, rep, conversation_id, message
        )
    yield _sse({"type": "start", "conversation_id": str(convo["id"])})
    for reason in skipped:
        yield _sse({"type": "notice", "message": f"Could not read {reason}"})

    if not resuming:
        # A thread with an unanswered interrupt will interrupt again on the very
        # next superstep, so sending a new message into one gets the rep nowhere
        # and looks broken. Re-present the card instead of starting a turn that
        # cannot finish.
        stranded = await asyncio.to_thread(
            convo_service.pending_approval_for, rep, str(convo["id"])
        )
        if stranded:
            yield _sse(
                {
                    "type": "notice",
                    "message": (
                        "There is a draft waiting for your approval in this chat. "
                        "Approve or reject it before sending another message."
                    ),
                }
            )
            yield _sse(
                {
                    "type": "approval_required",
                    "conversation_id": str(convo["id"]),
                    **stranded,
                }
            )
            return

    # The POOL, not a connection: handlers check one out per call. Pinning a
    # connection here held it hostage for the whole stream — 10-60s a turn, of
    # which ~97.5% is model time — so ten concurrent chats exhausted the pool.
    tool_specs = registry.build(rep, db.ro_pool())
    vintage = ", ".join(sorted({v for _t, v, _n in await asyncio.to_thread(db.data_vintage)})) or "unknown"

    # The agent's callbacks are the producer; this generator is the consumer.
    async def on_text_delta(delta: str) -> None:
        await queue.put({"type": "token", "delta": delta})

    async def on_tool_start(call_id: str, name: str, args: dict) -> None:
        await queue.put({"type": "tool_start", "call_id": call_id, "name": name, "input": args})

    async def on_tool_end(
        call_id: str, name: str, args: dict, output: str, is_error: bool, duration_ms: float
    ) -> None:
        await queue.put(
            {
                "type": "tool_end",
                "call_id": call_id,
                "name": name,
                "input": args,
                "output": output,
                "is_error": is_error,
                "duration_ms": round(duration_ms, 1),
            }
        )

    # The three agents, at the one call site. The tool NAMES decide the binding:
    # graph.py binds exactly these to the `agenda` node and everything else to
    # `agent`, and the reviewer is only reached on a gated round.
    agents = {
        "agenda_instructions": agent.build_agenda_instructions(rep, vintage),
        "agenda_tools": AGENDA_TOOL_NAMES,
        "reviewer": compliance.review_calls,
    }

    async def drive() -> None:
        try:
            common = {
                "ctx": rep,
                "tool_specs": tool_specs,
                # The checkpointer thread. It is the conversation uuid, which
                # get_or_create (or owned_by, on a resume) has already proven
                # this rep owns — never a client-supplied value. CLAUDE.md §1.8.
                "thread_id": str(convo["id"]),
                "vintage_summary": vintage,
                "on_text_delta": on_text_delta,
                "on_tool_start": on_tool_start,
                "on_tool_end": on_tool_end,
                "checkpointer": checkpointer(),
                **agents,
            }
            if resuming:
                result = await graph.resume_turn(decision=decision or {}, **common)
            else:
                result = await graph.run_turn(
                    user_message=message, images=images or None, **common
                )
            await queue.put({"__result__": result})
        except Exception as exc:  # noqa: BLE001 — reported to the client as an event
            await queue.put({"__error__": exc})
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(drive(), name="agent-turn")
    result = None
    failure: Exception | None = None
    tool_events: list[dict] = []

    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            if "__result__" in item:
                result = item["__result__"]
                continue
            if "__error__" in item:
                failure = item["__error__"]
                continue
            if item["type"] == "tool_end":
                tool_events.append(item)
            yield _sse(item)
    except asyncio.CancelledError:
        # The client disconnected (Stop button, closed tab). Cancel the turn
        # rather than letting it run on and bill tokens nobody will read.
        task.cancel()
        raise
    finally:
        if not task.done():
            task.cancel()

    if failure is not None:
        log.exception("turn failed", exc_info=failure)
        metrics.incr("turn_failed")
        yield _sse({"type": "error", "message": _explain(failure)})
        return

    if result is None:
        yield _sse({"type": "error", "message": "The turn produced no result."})
        metrics.incr("turn_empty")
        return

    if result.interrupt is not None:
        # THE TURN HAS NOT PRODUCED AN ANSWER. Everything below this branch
        # describes a completed turn and would either lie or corrupt the thread:
        #
        #   check_grounding / check_citations — a verdict over half an answer is
        #     noise, and record_turn needs a verdict, so both wait for the resume
        #   record_turn  — would write an assistant row now and a DUPLICATE user
        #     row when the resume leg runs; record_pause writes one row that the
        #     resume completes in place
        #   metrics.record_turn — would count a turn that has not happened
        #   done         — the turn is not done
        await asyncio.to_thread(
            convo_service.record_pause, convo["id"], rep, message, result, tool_events
        )
        metrics.incr("approval_requested")
        await audit.audit_logger.log(
            event="approval_requested",
            chair_id=rep.chair_id,
            rep_code=rep.rep_code,
            conversation_id=str(convo["id"]),
            question=message,
            # The PRE-EDIT draft, preserved here and nowhere else. It is what
            # makes an edit provable after the fact, because the approved args
            # replace these in the graph state.
            drafted=result.interrupt.get("calls"),
            review=result.interrupt.get("review"),
            interrupt_id=result.interrupt.get("interrupt_id"),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            model=agent.DEFAULT_MODEL,
        )
        yield _sse(
            {
                "type": "approval_required",
                "conversation_id": str(convo["id"]),
                **result.interrupt,
            }
        )
        return

    # Regression signal, deliberately not shown to the rep: the model is told
    # never to name tables or columns, and this measures whether it obeyed.
    leaked = guardrails.check_internal_disclosure(
        result.final_text, set(schema.internal_names())
    )
    if leaked:
        metrics.incr("internal_disclosure")
        log.warning("internal identifiers leaked into an answer", extra={"leaked": leaked})

    # "Cite or refuse", measured rather than requested. The tool description asks
    # for citations; a prompt rule can be talked around, so the outcome is
    # checked. A clinical claim traced to no source is the failure mode that
    # matters most in this product.
    citation = guardrails.check_citations(result.final_text, _retrieved_passages(tool_events))
    if not citation["cited"]:
        yield _sse(
            {
                "type": "notice",
                "message": (
                    "This answer draws on product literature but does not name its "
                    "source. Ask for the document and section before relying on it."
                ),
            }
        )
        metrics.incr("uncited_literature_answers")
        log.warning("uncited answer from retrieved literature", extra=citation)

    verdict = guardrails.check_grounding(result.final_text, result.tool_results_text)
    if not verdict["grounded"]:
        yield _sse(
            {
                "type": "grounding",
                "grounded": False,
                "unverified_claims": verdict["unverified_claims"],
            }
        )
        metrics.incr("ungrounded_answers")

    if result.hit_round_cap:
        yield _sse(
            {
                "type": "notice",
                "message": (
                    f"Stopped after {agent.MAX_TOOL_ROUNDS} tool rounds. "
                    "The answer may be incomplete — try a narrower question."
                ),
            }
        )
        metrics.incr("tool_round_cap_hit")

    total_ms = (time.perf_counter() - started) * 1000
    if resuming:
        # Completes the paused row rather than appending a second assistant
        # message — and clearing pending_approval is what un-wedges the thread.
        completed = await asyncio.to_thread(
            convo_service.record_resume, convo["id"], rep, result, tool_events, verdict
        )
        if not completed:
            # The projection and the graph state disagreed. Clear the marker so
            # the conversation is usable rather than stuck behind a card that no
            # longer corresponds to anything.
            await asyncio.to_thread(
                convo_service.clear_pending_approval, rep, str(convo["id"])
            )
        # The decision, paired with the `approval_requested` record above. Those
        # two together are the feature's compliance artefact: what was drafted,
        # what the reviewer said, what the rep decided, and what changed.
        await audit.audit_logger.log(
            event="approval_decided",
            chair_id=rep.chair_id,
            rep_code=rep.rep_code,
            conversation_id=str(convo["id"]),
            approved=bool((decision or {}).get("approved")),
            edited_fields=sorted(
                {
                    field
                    for patch in ((decision or {}).get("edits") or {}).values()
                    for field in patch
                }
            ),
            interrupt_id=(drafted or {}).get("interrupt_id"),
            review=(drafted or {}).get("review"),
            tool_trace=[
                {"tool": t.name, "ms": round(t.duration_ms, 1), "error": t.is_error}
                for t in result.tool_trace
            ],
            latency_ms=round(total_ms, 1),
            model=agent.DEFAULT_MODEL,
        )
    else:
        await asyncio.to_thread(
            convo_service.record_turn,
            convo["id"],
            rep,
            message,
            result,
            tool_events,
            verdict,
        )
    metrics.record_turn(
        total_ms=total_ms,
        tool_total_ms=result.tool_ms,
        tools=[(t.name, t.is_error) for t in result.tool_trace],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cached_tokens=result.cached_tokens,
    )
    await audit.audit_logger.log(
        chair_id=rep.chair_id,
        rep_code=rep.rep_code,
        conversation_id=str(convo["id"]),
        response_id=result.response_id,
        question=message,
        images_attached=[i["name"] for i in images],
        answer=result.final_text,
        grounded=verdict["grounded"],
        unverified_claims=verdict["unverified_claims"],
        internal_disclosure=leaked,
        tool_trace=[
            {"tool": t.name, "ms": round(t.duration_ms, 1), "error": t.is_error}
            for t in result.tool_trace
        ],
        latency_ms=round(total_ms, 1),
        tool_ms=round(result.tool_ms, 1),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cached_tokens=result.cached_tokens,
        model=agent.DEFAULT_MODEL,
    )

    yield _sse(
        {
            "type": "done",
            "conversation_id": str(convo["id"]),
            "response_id": result.response_id,
            "usage": {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cached_tokens": result.cached_tokens,
            },
            "timing": {
                "total_ms": round(total_ms, 1),
                # Time inside tool handlers — DB, Gmail, embeddings — named for
                # what it measures. Surfaced per turn because it is the number
                # people get wrong: the tools are a rounding error next to the
                # model.
                "tool_ms": round(result.tool_ms, 1),
                "tool_share_pct": round(100 * result.tool_ms / total_ms, 2) if total_ms else None,
            },
        }
    )


def _retrieved_passages(tool_events: list[dict]) -> list[dict]:
    """The {document, section} pairs search_literature returned this turn.

    Read back out of the tool events rather than threaded through the agent, so
    the graph core stays unaware that retrieval exists — the same reason the tool
    layer is a registry rather than a special case in the loop.
    """
    passages: list[dict] = []
    for event in tool_events:
        if event.get("name") != "search_literature":
            continue
        try:
            payload = json.loads(event.get("output") or "{}")
        except (TypeError, ValueError):
            continue
        for row in payload.get("rows") or []:
            if isinstance(row, dict):
                passages.append(
                    {"document": row.get("document"), "section": row.get("section")}
                )
    return passages


def _explain(exc: Exception) -> str:
    """Client-facing message. Never leak an internal detail into the stream."""
    name = type(exc).__name__
    if name in {"RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"}:
        return "The model is temporarily unavailable. Please try again in a moment."
    return "Something went wrong handling that message. The error has been logged."
