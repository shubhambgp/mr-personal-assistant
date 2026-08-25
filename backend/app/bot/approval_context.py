"""What a gated tool handler may know about the round it runs in.

Two facts are produced upstream of the tool node and needed inside a write
handler, and NEITHER may become a tool parameter:

  * the literature passages retrieved this turn — services/agenda.send_mail
    re-runs check_outbound on the final bytes, and without the passages every
    clinical claim looked uncited, so the compliant clinical-email flow could
    only ever be blocked (the fail was closed, but the feature was dead);
  * whether the rep edited the draft at the approval gate — the outbound log's
    edited_by_rep column recorded False unconditionally, in the one artefact
    whose purpose is "what was sent, and did a human change it".

A tool parameter is out for the same reason chair_id is (CLAUDE.md §1.2): the
model composes tool arguments, and neither of these is the model's to assert.
Graph state is out too — handlers never see state; ToolNode hands them only
their schema arguments.

So the graph's tools node sets these ContextVars from state immediately before
dispatching, and the handlers read them back. ContextVars set in the wrapper
coroutine propagate into the child tasks ToolNode creates (children copy the
context at creation, which is after the set), and each graph invocation runs in
its own context, so two reps' concurrent turns cannot see each other's values.

`turn_edited` is round-level, not per-call: a handler does not know its own
call id, and the agenda prompt already requires a gated call to be issued alone
in its round. The imprecision this buys — two gated calls in one round would
share the flag — is documented here so nobody narrows it by adding a call-id
parameter, which would be the model asserting identity again.
"""

from __future__ import annotations

from contextvars import ContextVar

#: {document, section, text} rows from every search_literature result earlier in
#: this turn — the same shape graph._literature_in mines from the transcript.
turn_passages: ContextVar[tuple[dict, ...]] = ContextVar("turn_passages", default=())

#: True when the rep changed any gated call's arguments at the approval gate.
turn_edited: ContextVar[bool] = ContextVar("turn_edited", default=False)
