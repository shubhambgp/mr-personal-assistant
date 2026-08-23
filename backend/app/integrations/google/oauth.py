"""The OAuth 2.0 authorization-code flow, and the `state` token that guards it.

Least privilege, and each scope justified because a refresh token is only as
dangerous as what it can do:

  openid, email        the connected address, so ctx.email_account is knowable.
  gmail.readonly       triage plus reading one thread. THE LARGEST exposure here
                       — it is the whole mailbox — which is why
                       AGENDA_GMAIL_SCOPE=metadata exists as an alternative:
                       headers, labels and thread structure only, which is
                       enough for the entire triage view because the categories
                       are computed from who sent the last message and when.
  gmail.send           send as the rep. Cannot read, list, label or delete. A
                       near-perfect match for a human-gated write.
  calendar.events      read and create events. NOT `calendar`, which would also
                       expose calendar settings and sharing.

Deliberately NOT requested:
  gmail.modify         would allow mark-as-read and labelling. We never mutate
                       the mailbox — that is *why* triage is derived rather than
                       stored — so a stolen token cannot alter the rep's mail or
                       make a doctor's message disappear.
  gmail.compose        would allow saving drafts in Gmail. Drafts live in the
                       approval card and the checkpointed conversation instead.
"""

from __future__ import annotations

import logging
import secrets
import time
from urllib.parse import urlencode

import jwt

from ...config import settings
from .client import GoogleError, request

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — a URL, not a secret
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

log = logging.getLogger(__name__)

_BASE_SCOPES = ("openid", "email", "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/calendar.events")

#: Distinct issuer so a consent-flow state token can never be mistaken for — or
#: replayed as — a session token, even though both are signed with JWT_SECRET.
_STATE_ISS = "qorvexa-agenda-state"
#: A consent screen left open for ten minutes has been abandoned.
_STATE_TTL_SECONDS = 600


def scopes() -> list[str]:
    read = (
        "https://www.googleapis.com/auth/gmail.readonly"
        if settings.agenda_gmail_scope == "readonly"
        else "https://www.googleapis.com/auth/gmail.metadata"
    )
    return [*_BASE_SCOPES, read]


def issue_state(chair_id: int) -> str:
    """CSRF protection for the redirect, with no table and no session store.

    `state` binds the callback to the rep who STARTED the flow. Without it, an
    attacker who can make the rep's browser hit /callback carrying the attacker's
    own `code` connects THEIR mailbox to the rep's chair_id — the reverse of the
    usual login CSRF, and it would silently point the entire agenda feature at an
    inbox the attacker controls.

    Signed with the app's JWT secret, so there is nothing to store and nothing to
    expire by hand.
    """
    now = int(time.time())
    return jwt.encode(
        {
            "chair_id": chair_id,
            "nonce": secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + _STATE_TTL_SECONDS,
            "iss": _STATE_ISS,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def read_state(token: str) -> int:
    """The chair_id the flow was started by. Raises ValueError if not trustworthy."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=_STATE_ISS,
            options={"require": ["exp", "iat", "iss"]},
        )
    except jwt.PyJWTError as exc:
        # No distinction between expired, tampered and malformed, matching
        # core/security.decode_token.
        raise ValueError("invalid state") from exc
    chair_id = claims.get("chair_id")
    if not isinstance(chair_id, int):
        raise ValueError("invalid state")
    return chair_id


def authorize_url(chair_id: int) -> str:
    return f"{AUTH_ENDPOINT}?" + urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes()),
            # offline + consent is what returns a refresh token at all. Without
            # `prompt=consent` Google omits it on a re-authorisation, and the
            # feature then works until the first access token expires.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": issue_state(chair_id),
        }
    )


async def exchange_code(code: str) -> dict:
    """Authorization code -> tokens. Returns Google's body verbatim."""
    return await request(
        "POST",
        TOKEN_ENDPOINT,
        form={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
    )


async def refresh_access_token(refresh_token: str) -> dict:
    return await request(
        "POST",
        TOKEN_ENDPOINT,
        form={
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
        },
    )


async def revoke(token: str) -> None:
    """Best-effort revocation at Google.

    A failure here must not stop the local delete: the rep asked to disconnect,
    and keeping their credential because Google was briefly unreachable is the
    wrong way to fail. Google also returns 400 for an already-invalid token,
    which is success for our purposes.
    """
    try:
        await request("POST", REVOKE_ENDPOINT, form={"token": token})
    except GoogleError as exc:
        # Logged, not suppressed silently: if revocation keeps failing, the
        # tokens we deleted locally are still live at Google and someone should
        # know. No token or address in the log line.
        log.warning("google token revocation failed", extra={"reason": type(exc).__name__})


async def userinfo(access_token: str) -> dict:
    return await request("GET", USERINFO_ENDPOINT, access_token=access_token)
