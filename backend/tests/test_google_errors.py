"""Google's two error-body shapes, and the one code that must be acted on.

The whole reason GoogleError carries a `code` is that a 400 from the OAuth token
endpoint is ambiguous in the most dangerous possible way:

  * invalid_grant  -> THIS rep's stored grant is dead. Delete it, ask them to
                      reconnect.
  * invalid_client -> the OPERATOR's client secret is wrong. Touch nothing.

Treating the second as the first would delete every rep's credential across the
whole deployment on the first request after a bad deploy, and consent cannot be
restored server-side — all 25 reps would have to reconnect by hand.
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations.google.client import GoogleError, request


def _transport(status: int, body: dict | str):
    def handler(_req: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


async def _call(monkeypatch, status: int, body: dict | str) -> GoogleError:
    transport = _transport(status, body)
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    with pytest.raises(GoogleError) as caught:
        await request("POST", "https://oauth2.googleapis.com/token", form={"x": "y"})
    return caught.value


@pytest.mark.asyncio
async def test_the_oauth_shape_yields_its_code_and_description(monkeypatch):
    """RFC 6749: `error` is a bare STRING, not an object.

    The old single expression did `(json()["error"] or {}).get("message")`, which
    raised AttributeError on a str — caught, so every token failure collapsed to
    "Google returned 400." with the one useful field discarded.
    """
    exc = await _call(
        monkeypatch,
        400,
        {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
    )
    assert exc.code == "invalid_grant"
    assert "expired or revoked" in str(exc)
    assert exc.status == 400
    assert not exc.retryable


@pytest.mark.asyncio
async def test_invalid_client_is_a_different_code(monkeypatch):
    """Same status, same shape, opposite meaning. The code is what separates them."""
    exc = await _call(monkeypatch, 400, {"error": "invalid_client"})
    assert exc.code == "invalid_client"
    assert exc.code != "invalid_grant"


@pytest.mark.asyncio
async def test_the_rest_api_shape_still_yields_its_message(monkeypatch):
    """Gmail and Calendar nest an object. Reading one shape broke the other."""
    exc = await _call(
        monkeypatch,
        401,
        {"error": {"code": 401, "message": "Invalid Credentials", "status": "UNAUTHENTICATED"}},
    )
    assert exc.code == "UNAUTHENTICATED"
    assert "Invalid Credentials" in str(exc)


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_does_not_raise_from_the_parser(monkeypatch):
    """A proxy's HTML error page must produce a GoogleError, not a TypeError."""
    exc = await _call(monkeypatch, 502, "<html>bad gateway</html>")
    assert exc.code is None
    assert exc.status == 502
    assert exc.retryable  # 502 is worth another go


@pytest.mark.asyncio
async def test_a_json_body_that_is_not_an_object_is_tolerated(monkeypatch):
    exc = await _call(monkeypatch, 400, {"error": ["unexpected"]})
    assert exc.code is None


@pytest.mark.asyncio
async def test_the_error_body_is_never_logged(monkeypatch, caplog):
    """Google echoes the request in its error bodies, which for a send is the
    draft. Only the status, the code and the last URL segment may be logged.

    `error_description` IS surfaced on the exception, and that is deliberate: it
    is Google's own sentence about what went wrong, not a copy of what we sent.
    """
    caplog.set_level("WARNING")
    await _call(
        monkeypatch,
        400,
        {
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked.",
            "secret_echo": "Dear Dr Sharma, the confidential draft body",
        },
    )
    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "Dr Sharma" not in logged
    assert "confidential draft body" not in logged
    assert "invalid_grant" in logged  # the code is safe and worth having
