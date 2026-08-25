"""Auth primitives: hashing, token verification, rate limiting."""

from __future__ import annotations

import time

import jwt
import pytest

from app.config import settings
from app.core.security import (
    InvalidToken,
    RateLimiter,
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)


def test_production_requires_a_secure_cookie():
    """A production deploy that forgets COOKIE_SECURE must fail at startup, not

    ship the session JWT over plaintext HTTP (audit finding M-SEC6).
    """
    from app.config import Settings

    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(jwt_secret="x" * 32, environment="production", cookie_secure=False)

    # The same config with a secure cookie is fine.
    ok = Settings(jwt_secret="x" * 32, environment="production", cookie_secure=True)
    assert ok.cookie_secure is True


def test_password_round_trip():
    digest = hash_password("correct horse")
    assert verify_password("correct horse", digest)
    assert not verify_password("wrong horse", digest)


def test_missing_hash_is_a_failure_not_a_pass():
    assert not verify_password("anything", None)
    assert not verify_password("anything", "")


def test_malformed_hash_fails_closed():
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_token_round_trip():
    token = issue_token(chair_id=7100001, rep_code=7800001, rep_name="A Rep")
    claims = decode_token(token)
    assert claims.chair_id == 7100001
    assert claims.rep_code == 7800001
    assert claims.rep_name == "A Rep"


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode(
        {
            "sub": "7800001",
            "chair_id": 999,
            "rep_code": 999,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "iss": "qorvexa-mr-assistant",
        },
        "a-different-secret-entirely-still-long-enough",
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_token(forged)


def test_expired_token_is_rejected():
    past = int(time.time()) - 10
    expired = jwt.encode(
        {
            "sub": "7800001",
            "chair_id": 7100001,
            "rep_code": 7800001,
            "iat": past - 60,
            "exp": past,
            "iss": "qorvexa-mr-assistant",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_token(expired)


def test_token_without_chair_id_is_rejected():
    """A signed token is still not a valid one if the claims are wrong."""
    thin = jwt.encode(
        {
            "sub": "7800001",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "iss": "qorvexa-mr-assistant",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_token(thin)


def test_rate_limiter_blocks_after_the_cap_and_resets():
    limiter = RateLimiter(max_attempts=3, window_seconds=60)
    assert all(limiter.check("1.2.3.4") for _ in range(3))
    assert not limiter.check("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") > 0
    # A different client is unaffected.
    assert limiter.check("5.6.7.8")
    limiter.reset("1.2.3.4")
    assert limiter.check("1.2.3.4")


def test_rate_limiter_sweeps_expired_keys():
    """Keys are evicted once their window expires, not kept forever.

    The login limiter is keyed on client-supplied identifier strings, so before
    the sweep an unauthenticated caller cycling random identifiers grew the
    table without bound — reset() only fires on a successful login, and pruning
    only ever touched the key being checked.
    """
    limiter = RateLimiter(max_attempts=3, window_seconds=60)
    limiter._SWEEP_AT = 8  # keep the test small; production keeps 4096

    for i in range(8):
        assert limiter.check(f"identifier-{i}")
    assert len(limiter._hits) == 8

    # All eight windows expire; the next check crosses the threshold and sweeps.
    for window in limiter._hits.values():
        window[0] -= 61
    assert limiter.check("fresh-key")
    assert set(limiter._hits) == {"fresh-key"}

    # Behaviour for a live key is untouched by the sweep machinery.
    assert all(limiter.check("live") for _ in range(3))
    assert not limiter.check("live")
