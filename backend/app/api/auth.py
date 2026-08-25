"""Login, logout, and "who am I".

The login handler is the only place a password is checked and the only place a
token is minted. Two properties worth stating explicitly:

* No user enumeration. A wrong password and an unknown rep_code return the
  identical 401 with the identical body, and both do the same amount of bcrypt
  work (see core.security.verify_password).
* chair_id is looked up server-side from the reps table and signed into the
  token. The client never sends it and cannot influence it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from ..config import settings
from ..core.metrics import metrics
from ..core.security import (
    issue_token,
    login_identifier_limiter,
    login_limiter,
    verify_password,
)
from ..deps import CurrentRep, rw_conn

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger(__name__)

_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
)


class LoginRequest(BaseModel):
    # A rep signs in with the code printed on their own reporting — either
    # rep_code or chair_id works, because reps know themselves by both.
    identifier: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RepOut(BaseModel):
    chair_id: int
    rep_code: int
    rep_name: str


@router.post("/login", response_model=RepOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    conn=Depends(rw_conn),
) -> RepOut:
    client_ip = request.client.host if request.client else "unknown"
    identifier = payload.identifier.strip()

    # Two independent buckets. The per-identifier one stops per-account brute
    # force and cannot lock out other reps who share a proxy IP; the per-IP one
    # limits a single host hammering many accounts. Either tripping is a 429.
    for limiter, key in (
        (login_identifier_limiter, identifier),
        (login_limiter, client_ip),
    ):
        if not limiter.check(key):
            metrics.incr("login_rate_limited")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again later.",
                headers={"Retry-After": str(limiter.retry_after(key))},
            )

    # Both identifiers are numeric, so parse FIRST and compare integers — the
    # old `rep_code::text = %(id)s` cast the COLUMN and could never use an
    # index. A non-numeric identifier is simply an unknown rep; it still pays
    # the bcrypt verify below, so the timing and the identical-401 survive.
    rep = None
    try:
        as_number = int(identifier)
    except ValueError:
        as_number = None
    if as_number is not None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT chair_id, rep_code, rep_name, password_hash
                FROM reps
                WHERE rep_code = %(id)s OR chair_id = %(id)s
                LIMIT 1
                """,
                {"id": as_number},
            )
            rep = cur.fetchone()

    # Verify even when the rep does not exist, so the timing is the same.
    stored = rep["password_hash"] if rep else None
    if not verify_password(payload.password, stored) or rep is None:
        metrics.incr("login_failed")
        log.warning("login failed", extra={"identifier_len": len(payload.identifier)})
        raise _INVALID

    login_limiter.reset(client_ip)
    login_identifier_limiter.reset(identifier)
    metrics.incr("login_ok")

    token = issue_token(
        chair_id=rep["chair_id"],
        rep_code=rep["rep_code"],
        rep_name=rep["rep_name"] or str(rep["rep_code"]),
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,           # not readable from JavaScript -> XSS cannot steal it
        secure=settings.cookie_secure,
        samesite="lax",          # survives top-level navigation, blocks cross-site POST
        max_age=settings.jwt_ttl_hours * 3600,
        path="/",
    )
    return RepOut(
        chair_id=rep["chair_id"],
        rep_code=rep["rep_code"],
        rep_name=rep["rep_name"] or str(rep["rep_code"]),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, path="/")


@router.get("/me", response_model=RepOut)
def me(rep: CurrentRep) -> RepOut:
    return RepOut(chair_id=rep.chair_id, rep_code=rep.rep_code, rep_name=rep.rep_name)
