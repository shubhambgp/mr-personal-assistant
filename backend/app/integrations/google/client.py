"""One HTTP client for every Google call, and the access-token cache.

NOTHING IN THIS PACKAGE READS os.environ. Credentials are constructed explicitly
from `settings` and passed in. That is not a style preference: pydantic-settings
reads .env into `settings` WITHOUT exporting to os.environ, so any library doing
an environment or ADC lookup finds a value under the eval harness (which calls
load_dotenv itself) and nothing at all under uvicorn. That exact bug already
shipped here once with OPENAI_API_KEY — ENGINEERING_LOG 16 — and it is not
shipping again with a mailbox credential.
tests/test_agenda_google.py greps this package for os.environ and fails on a hit.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: Google's own guidance is 60s; 90s means we never present a token that expires
#: mid-flight on a slow request.
_EXPIRY_SKEW_SECONDS = 90

#: Access tokens live ~1 hour. Cached in-process and keyed by chair_id, so a
#: triage call and a send in the same turn do not each pay a refresh round trip.
#:
#: Per-process, like the login rate limiter, and with the same caveat: behind
#: multiple workers each process keeps its own. That is harmless here — the worst
#: case is a redundant refresh, and Google permits concurrent refreshes.
_access_cache: dict[int, tuple[str, float]] = {}


class GoogleError(RuntimeError):
    """A Google API call failed. Carries the status and code so callers can branch.

    `code` is Google's own machine-readable error name, and the only reason it
    exists is that ONE value of it must be acted on differently: `invalid_grant`
    from the token endpoint means the rep's stored grant is dead and they must
    reconnect. Branching on the status alone cannot express that — a 400 from the
    token endpoint is `invalid_grant` OR `invalid_client`, and those two demand
    opposite responses (delete the rep's token / do not touch anybody's token).
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable


def _error_fields(response: httpx.Response) -> tuple[str | None, str]:
    """Google's error code and short reason, from EITHER of its two body shapes.

    There are two, and reading only one is how `invalid_grant` was being thrown
    away. The REST APIs nest an object:

        {"error": {"code": 401, "message": "Invalid Credentials", "status": "UNAUTHENTICATED"}}

    The OAuth token endpoint follows RFC 6749, where `error` is a bare STRING:

        {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}

    The previous single expression did `(json()["error"] or {}).get("message")`,
    so the OAuth shape raised AttributeError on a str — caught, and every token
    failure collapsed to "Google returned 400." with the one code that matters
    discarded.

    `error_description` is Google's own sentence, not an echo of the request, so
    returning it keeps the rule that a request body is never surfaced or logged.
    """
    try:
        body = response.json()
    except ValueError:
        return None, ""
    if not isinstance(body, dict):
        return None, ""

    err = body.get("error")
    if isinstance(err, str):
        return err or None, str(body.get("error_description") or "")[:200]
    if isinstance(err, dict):
        code = str(err.get("status") or "") or None
        return code, str(err.get("message") or "")[:200]
    return None, ""


def cache_access_token(chair_id: int, token: str, expires_in: int) -> None:
    _access_cache[chair_id] = (token, time.monotonic() + max(0, expires_in - _EXPIRY_SKEW_SECONDS))


def cached_access_token(chair_id: int) -> str | None:
    entry = _access_cache.get(chair_id)
    if entry is None:
        return None
    token, good_until = entry
    if time.monotonic() >= good_until:
        _access_cache.pop(chair_id, None)
        return None
    return token


def forget_access_token(chair_id: int) -> None:
    """Called on disconnect and on a 401, so a stale token is never re-presented."""
    _access_cache.pop(chair_id, None)


#: ONE client for every Google call, created lazily and closed by
#: bootstrap.close_resources(). A client per request paid a fresh TCP+TLS
#: handshake every time — a 25-thread triage was 25 handshakes before any mail
#: was read. Tests inject a MockTransport by monkeypatching `_client` directly.
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def close_http() -> None:
    """Called from bootstrap.close_resources(), so the app and the eval harness
    both close it — the whole reason bootstrap.py exists."""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


async def request(
    method: str,
    url: str,
    *,
    access_token: str | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    timeout_seconds: float = 20.0,
) -> dict:
    """One Google call. Returns the parsed body, or raises GoogleError.

    A short timeout on purpose: these calls sit inside a chat turn a rep is
    watching, so a hung mailbox must fail fast and be reported rather than hold
    the stream open.
    """
    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    try:
        response = await _http().request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            data=form,
            timeout=timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        raise GoogleError("Google did not respond in time.", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise GoogleError(f"Could not reach Google: {type(exc).__name__}", retryable=True) from exc

    if response.status_code >= 400:
        # Google's error bodies quote the request, which for a send would mean
        # the draft. Log the status and its own reason string, never the body.
        code, reason = _error_fields(response)
        log.warning(
            "google api error",
            extra={"status": response.status_code, "code": code, "url_path": url.rsplit("/", 1)[-1]},
        )
        raise GoogleError(
            reason or f"Google returned {response.status_code}.",
            status=response.status_code,
            code=code,
            retryable=response.status_code in {429, 500, 502, 503, 504},
        )

    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise GoogleError("Google returned a response that was not JSON.") from exc
