"""POST /api/auth/login through the real transport.

tests/test_security.py proves the primitives (bcrypt, JWT, the limiter as a
class); nothing proved the ENDPOINT — cookie flags actually set, the identical
401 for wrong-password and unknown-rep, the 429 with its Retry-After. The eval
harness bypasses HTTP entirely (ENGINEERING_LOG 16), so this is the only place
those claims are exercised as a client sees them.

DB-light: `rw_conn` is dependency-overridden with a fake connection serving one
seeded rep row, so no PostgreSQL is needed and the suite stays in the fast tier.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import (
    hash_password,
    login_identifier_limiter,
    login_limiter,
)
from app.deps import rw_conn
from app.main import app

REP_ROW = {
    "chair_id": 7100001,
    "rep_code": 7800001,
    "rep_name": "Test Rep",
    # Hashed once at import: bcrypt is deliberately slow, and every test shares it.
    "password_hash": hash_password("secret123"),
}


class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, _sql, params=None):
        self._hit = params and params.get("id") in (REP_ROW["rep_code"], REP_ROW["chair_id"])

    def fetchone(self):
        return dict(REP_ROW) if self._hit else None


class _FakeConn:
    def cursor(self, **_kwargs):
        return _FakeCursor()


@pytest.fixture
def client():
    app.dependency_overrides[rw_conn] = lambda: _FakeConn()
    # Module-level limiters carry state between tests; a leftover bucket would
    # make these order-dependent.
    login_limiter._hits.clear()
    login_identifier_limiter._hits.clear()
    # No context manager: entering it would run the lifespan and open real pools.
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, identifier="7800001", password="secret123"):
    return client.post("/api/auth/login", json={"identifier": identifier, "password": password})


def test_login_sets_the_session_cookie_with_the_right_flags(client):
    response = _login(client)
    assert response.status_code == 200
    assert response.json() == {"chair_id": 7100001, "rep_code": 7800001, "rep_name": "Test Rep"}

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie, "JS must not be able to read the token"
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "max-age=" in cookie


def test_wrong_password_and_unknown_rep_are_byte_identical(client):
    """No user enumeration: the two failures must be indistinguishable."""
    wrong_password = _login(client, password="nope")
    unknown_rep = _login(client, identifier="9999999")
    non_numeric = _login(client, identifier="'; DROP TABLE reps; --")

    assert wrong_password.status_code == unknown_rep.status_code == 401
    assert wrong_password.content == unknown_rep.content == non_numeric.content


def test_chair_id_works_as_the_identifier_too(client):
    assert _login(client, identifier="7100001").status_code == 200


def test_the_sixth_rapid_attempt_is_throttled_with_retry_after(client):
    for _ in range(5):
        assert _login(client, password="nope").status_code == 401

    throttled = _login(client, password="nope")
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) > 0
    # And the correct password does not bypass the bucket.
    assert _login(client).status_code == 429


def test_the_identifier_bucket_trips_independently_of_the_ip(client):
    """Rotating source IPs against ONE account still gets stopped."""
    for attempt in range(login_identifier_limiter.max_attempts):
        # Clear only the shared-IP bucket, simulating a fresh IP per attempt.
        login_limiter._hits.clear()
        response = _login(client, password="nope")
        assert response.status_code == 401, f"attempt {attempt} throttled too early"

    login_limiter._hits.clear()
    assert _login(client, password="nope").status_code == 429


def test_a_success_resets_the_buckets(client):
    for _ in range(4):
        _login(client, password="nope")
    assert _login(client).status_code == 200
    # The failed attempts were forgiven; the window starts over.
    for _ in range(5):
        assert _login(client, password="nope").status_code == 401
