"""FastAPI dependencies: who is asking, and what they may touch.

`current_rep` is the only place a RepContext is constructed. It reads the JWT
from an httpOnly cookie or an Authorization header, verifies it, and returns a
frozen context. Nothing downstream accepts a chair_id from the client, so there
is exactly one path by which identity enters the system.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status

from .bot import db
from .bot.context import RepContext
from .config import settings
from .core.logging import chair_id_var
from .core.security import InvalidToken, decode_token

log = logging.getLogger(__name__)

_UNAUTHORISED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def _token_from(cookie: str | None, authorization: str | None) -> str:
    """Cookie first (the browser path), Bearer second (curl, CI, the eval harness)."""
    if cookie:
        return cookie
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    raise _UNAUTHORISED


async def current_rep(
    session: Annotated[str | None, Cookie(alias=settings.cookie_name)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> RepContext:
    token = _token_from(session, authorization)
    try:
        claims = decode_token(token)
    except InvalidToken:
        # Deliberately no distinction between expired, tampered and malformed.
        raise _UNAUTHORISED from None

    chair_id_var.set(claims.chair_id)
    return RepContext(
        chair_id=claims.chair_id,
        rep_code=claims.rep_code,
        rep_name=claims.rep_name,
    )


CurrentRep = Annotated[RepContext, Depends(current_rep)]


async def agenda_rep(rep: CurrentRep) -> RepContext:
    """`current_rep` plus the connected mailbox, for routes that touch the agenda.

    A SEPARATE dependency rather than an addition to current_rep, for two
    reasons: it costs a query, and /api/health and /api/auth/me have no business
    making it.

    THE MAILBOX IS A SERVER-SIDE LOOKUP KEYED ON A VERIFIED CLAIM, not a claim
    inside the token. CLAUDE.md §1.7 used to say "populated from the verified
    token"; it has been amended, because a claim baked into an 8-hour JWT cannot
    be withdrawn — a rep who disconnects at 09:05 would still be asserting the
    address at 17:00, and there is no refresh rotation or revocation list. A
    stored connection is revoked in one statement.

    Both properties §1.7 actually cares about survive: the model cannot name a
    mailbox, and the mailbox is not client-supplied. Identity still enters the
    system exactly once — as chair_id — and this is still the only file that
    builds a RepContext.
    """
    if not settings.agenda_configured:
        return rep
    from .services import agenda as agenda_service

    try:
        connection = await asyncio.to_thread(
            agenda_service.connection, rep.chair_id, rep.rep_code
        )
    except Exception:  # noqa: BLE001 — an agenda lookup must not break the request
        log.warning("agenda connection lookup failed", exc_info=True)
        return rep
    if connection is None or connection.stale:
        # `stale` counts as unconnected HERE on purpose. The row still exists so
        # Settings can name the account to reconnect, but the credential is gone,
        # so every mail and calendar tool would fail if offered. Leaving
        # email_account None shrinks the tool list to the ones that still work;
        # agenda_status looks the state up itself and reports "expired" rather
        # than "never connected".
        return rep
    return dataclasses.replace(rep, email_account=connection.email_account)


AgendaRep = Annotated[RepContext, Depends(agenda_rep)]


def ro_conn() -> Iterator:
    """A read-only connection for the duration of one request.

    One checkout per request rather than per query: a briefing runs five or six
    queries, and they should share a connection the way the previous
    session-scoped cursor did.
    """
    with db.ro_pool().connection() as conn:
        yield conn


def rw_conn() -> Iterator:
    with db.rw_pool().connection() as conn:
        yield conn


RoConn = Annotated[object, Depends(ro_conn)]
RwConn = Annotated[object, Depends(rw_conn)]
