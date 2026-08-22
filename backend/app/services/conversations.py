"""Conversation and message persistence.

EVERY query here filters on chair_id from the verified RepContext — never on an
id the client supplied alone. `conversation_id` arrives from the browser, so it
is treated as a claim to be checked, not a fact: the WHERE clause always pairs
it with the rep's chair_id, so asking for someone else's conversation returns
nothing rather than their transcript.

WHY EVERYTHING HERE USES THE READ-WRITE POOL, EVEN THE READS
------------------------------------------------------------
The obvious alternative — read history through the read-only pool — requires
granting `qorvexa_ro` SELECT on public.conversations. Do not do that.

`qorvexa_ro` is the role the agent's `run_sql` tool connects as. Its denylist is
built from the data manifest, and the chat-history tables are not in the
manifest — so `SELECT * FROM conversations` would pass every guard in
sql_tools.py. The only thing standing between a model-composed query and every
rep's transcript is that this role has no privileges in the `public` schema.

Keeping the grant absent is the boundary. `evals/test_guardrails.py::
test_run_sql_cannot_reach_chat_history` asserts it stays that way.
"""

from __future__ import annotations

import json
import logging

from psycopg.rows import dict_row

from ..bot import db
from ..bot.context import RepContext

log = logging.getLogger(__name__)
TITLE_MAX = 60


def _title_from(message: str) -> str:
    text = " ".join(message.split())
    if len(text) <= TITLE_MAX:
        return text or "New conversation"
    return text[: TITLE_MAX - 1].rstrip() + "…"


def get_or_create(rep: RepContext, conversation_id: str | None, first_message: str) -> dict:
    with db.rw_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if conversation_id:
            cur.execute(
                "SELECT id FROM public.conversations WHERE id = %s AND chair_id = %s",
                (conversation_id, rep.chair_id),
            )
            found = cur.fetchone()
            if found:
                return found
            # Unknown or not theirs — fall through and start a fresh one rather
            # than 404ing mid-stream.
            log.warning("conversation not found for chair; starting a new one")

        cur.execute(
            "INSERT INTO public.conversations (chair_id, title) VALUES (%s, %s) "
            "RETURNING id",
            (rep.chair_id, _title_from(first_message)),
        )
        return cur.fetchone()


def _trim(tool_events: list[dict]) -> list[dict]:
    """The tool timeline as the UI renders it. Shared by every writer here."""
    return [
        {
            "call_id": e.get("call_id"),
            "name": e.get("name"),
            "input": e.get("input"),
            "output": e.get("output"),
            "is_error": e.get("is_error"),
            "duration_ms": e.get("duration_ms"),
        }
        for e in tool_events
    ]


def record_turn(
    conversation_id,
    rep: RepContext,
    question: str,
    result,
    tool_events: list[dict],
    verdict: dict,
) -> None:
    """Persists the user message, the assistant message, and the tool timeline.

    The tool timeline is stored so a resumed conversation shows the same
    intermediate steps the rep originally saw — not just the final text.
    """
    trimmed = _trim(tool_events)
    # One transaction: the pools are autocommit, so without this a failure
    # between the two INSERTs would leave a user message with no assistant reply
    # — a transcript that looks permanently wedged.
    with db.rw_pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.messages (conversation_id, role, content) VALUES (%s,'user',%s)",
            (conversation_id, question),
        )
        cur.execute(
            "INSERT INTO public.messages "
            "(conversation_id, role, content, tool_calls, grounded, unverified_claims) "
            "VALUES (%s,'assistant',%s,%s,%s,%s)",
            (
                conversation_id,
                result.final_text,
                json.dumps(trimmed, default=str),
                verdict["grounded"],
                json.dumps(verdict["unverified_claims"]),
            ),
        )
        # `previous_response_id` is deliberately no longer written. Continuity
        # is the LangGraph checkpointer's thread (keyed on this conversation's
        # id), so the column is vestigial — kept nullable for one release so a
        # rollback has somewhere to land, then dropped.
        cur.execute(
            "UPDATE public.conversations SET updated_at = now() "
            "WHERE id = %s AND chair_id = %s",
            (conversation_id, rep.chair_id),
        )



def owned_by(rep: RepContext, conversation_id: str) -> bool:
    """Strict ownership. Used by the resume endpoint, where get_or_create is wrong.

    `get_or_create` deliberately creates a fresh conversation when the id is not
    the caller's, which is right mid-stream for a new message and WRONG here: it
    would silently fork an empty thread and swallow the resume instead of
    refusing it. A resume of someone else's conversation must 404.
    """
    with db.rw_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM public.conversations WHERE id = %s AND chair_id = %s",
            (conversation_id, rep.chair_id),
        )
        return cur.fetchone() is not None


def record_pause(
    conversation_id,
    rep: RepContext,
    question: str,
    result,
    tool_events: list[dict],
) -> None:
    """Persists a turn that stopped to ask a human.

    Written in the same shape record_turn uses — a user row and an assistant row
    with the tool timeline — so the reopen path needs no second renderer. The
    difference is that `grounded` stays NULL, because there is no answer to
    ground yet, and `pending_approval` carries what the rep must decide.
    """
    # One transaction, same reason as record_turn: a pause that persisted the
    # user row but not the assistant row (with its pending_approval) would wedge
    # the thread with no card to resume.
    with db.rw_pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.messages (conversation_id, role, content) VALUES (%s,'user',%s)",
            (conversation_id, question),
        )
        cur.execute(
            "INSERT INTO public.messages "
            "(conversation_id, role, content, tool_calls, pending_approval) "
            "VALUES (%s,'assistant',%s,%s,%s)",
            (
                conversation_id,
                result.final_text,
                json.dumps(_trim(tool_events), default=str),
                json.dumps(result.interrupt, default=str),
            ),
        )
        cur.execute(
            "UPDATE public.conversations SET updated_at = now() WHERE id = %s AND chair_id = %s",
            (conversation_id, rep.chair_id),
        )


def record_resume(
    conversation_id,
    rep: RepContext,
    result,
    tool_events: list[dict],
    verdict: dict,
) -> bool:
    """Completes the paused row rather than inserting a second one.

    Appending a new assistant row would leave the transcript with two halves of
    one answer and a user message that was never repeated. So the paused row is
    finished in place: text appended, timeline merged, verdict recorded, and
    pending_approval cleared — which is also what un-wedges the conversation.
    """
    # One transaction: finishing the paused row and stamping the conversation
    # must both land, or the resume half-applies (answer saved, thread still
    # shows as updated at the wrong time — or vice versa).
    with (
        db.rw_pool().connection() as conn,
        conn.transaction(),
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            "SELECT id, tool_calls FROM public.messages "
            "WHERE conversation_id = %s AND pending_approval IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        )
        row = cur.fetchone()
        if row is None:
            return False
        merged = list(row["tool_calls"] or []) + _trim(tool_events)
        cur.execute(
            """UPDATE public.messages
                  SET content = CASE WHEN content = '' THEN %s
                                     ELSE content || E'\n\n' || %s END,
                      tool_calls = %s,
                      grounded = %s,
                      unverified_claims = %s,
                      pending_approval = NULL
                WHERE id = %s""",
            (
                result.final_text,
                result.final_text,
                json.dumps(merged, default=str),
                verdict["grounded"],
                json.dumps(verdict["unverified_claims"]),
                row["id"],
            ),
        )
        cur.execute(
            "UPDATE public.conversations SET updated_at = now() WHERE id = %s AND chair_id = %s",
            (conversation_id, rep.chair_id),
        )
    return True


def claim_pending_approval(
    rep: RepContext, conversation_id: str, interrupt_id: str
) -> dict | None:
    """Atomically claim the open approval for a resume, or return None.

    This is the concurrency boundary for /api/chat/resume. Reading the card and
    then acting on it in two steps let two tabs (or a network-retried
    double-click) both pass the check and both drive a resume — and since
    gmail.send has no idempotency key, that is a double-send. Here the ownership
    check, the interrupt-id match and the claim happen in ONE statement, so
    exactly one caller wins and the other gets None -> 409.

    The claim is a `resume_claimed_at` stamp inside the card rather than clearing
    pending_approval, because record_resume finds the paused row by
    `pending_approval IS NOT NULL` and must still be able to. A claim older than
    two minutes is treated as stale and may be re-claimed, so a resume that
    crashed mid-flight recovers on reload instead of wedging the thread forever —
    while two clicks milliseconds apart still cannot both win.
    """
    with db.rw_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """UPDATE public.messages m
                  SET pending_approval =
                      jsonb_set(m.pending_approval, '{resume_claimed_at}',
                                to_jsonb(now()::text))
                 FROM public.conversations c
                WHERE m.conversation_id = %s
                  AND c.id = m.conversation_id AND c.chair_id = %s
                  AND m.pending_approval IS NOT NULL
                  AND m.pending_approval->>'interrupt_id' = %s
                  AND (
                       m.pending_approval->'resume_claimed_at' IS NULL
                       OR (m.pending_approval->>'resume_claimed_at')::timestamptz
                          < now() - interval '2 minutes'
                  )
            RETURNING m.pending_approval""",
            (conversation_id, rep.chair_id, interrupt_id),
        )
        row = cur.fetchone()
    return row["pending_approval"] if row else None


def pending_approval_for(rep: RepContext, conversation_id: str) -> dict | None:
    """The open approval on this conversation, if any. Paired with chair_id."""
    with db.rw_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT m.pending_approval
                 FROM public.messages m
                 JOIN public.conversations c ON c.id = m.conversation_id
                WHERE m.conversation_id = %s AND c.chair_id = %s
                  AND m.pending_approval IS NOT NULL
                ORDER BY m.created_at DESC LIMIT 1""",
            (conversation_id, rep.chair_id),
        )
        row = cur.fetchone()
    return row["pending_approval"] if row else None


def clear_pending_approval(rep: RepContext, conversation_id: str) -> None:
    """Drop a stranded approval without answering it.

    Needed for the case where the graph state and this projection disagree — the
    checkpoint has moved on but the row still shows a card. Otherwise the
    conversation stays wedged behind a decision that no longer exists.
    """
    with db.rw_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE public.messages m SET pending_approval = NULL
                 WHERE m.conversation_id = %s AND m.pending_approval IS NOT NULL
                   AND EXISTS (SELECT 1 FROM public.conversations c
                                WHERE c.id = m.conversation_id AND c.chair_id = %s)""",
            (conversation_id, rep.chair_id),
        )


def list_for_rep(rep: RepContext, limit: int = 50) -> list[dict]:
    with db.rw_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT count(*) FROM public.messages m
                     WHERE m.conversation_id = c.id) AS message_count
            FROM public.conversations c
            WHERE c.chair_id = %s
            ORDER BY c.updated_at DESC
            LIMIT %s
            """,
            (rep.chair_id, limit),
        )
        return cur.fetchall()


def messages_for(rep: RepContext, conversation_id: str) -> list[dict] | None:
    """None when the conversation is not this rep's — the caller turns that into
    a 404, so a probe cannot distinguish "absent" from "someone else's"."""
    with db.rw_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT 1 FROM public.conversations WHERE id = %s AND chair_id = %s",
            (conversation_id, rep.chair_id),
        )
        if cur.fetchone() is None:
            return None
        cur.execute(
            "SELECT id, role, content, tool_calls, grounded, unverified_claims, "
            "pending_approval, created_at "
            "FROM public.messages WHERE conversation_id = %s ORDER BY created_at",
            (conversation_id,),
        )
        return cur.fetchall()


def rename(rep: RepContext, conversation_id: str, title: str) -> bool:
    with db.rw_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE public.conversations SET title = %s, updated_at = now() "
            "WHERE id = %s AND chair_id = %s",
            (title[:TITLE_MAX], conversation_id, rep.chair_id),
        )
        return cur.rowcount > 0


def delete(rep: RepContext, conversation_id: str) -> bool:
    with db.rw_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public.conversations WHERE id = %s AND chair_id = %s",
            (conversation_id, rep.chair_id),
        )
        return cur.rowcount > 0
