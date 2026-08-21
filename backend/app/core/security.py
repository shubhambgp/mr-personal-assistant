"""Password verification, JWT issue/verify, and login rate limiting.

The security boundary of this whole application is: chair_id comes from a
signed token and nowhere else. Everything here exists to make that true.

Not production-grade, and the gaps are deliberate and documented rather than
hidden — see README "Not built yet, and why":
  * the rate limiter is per-process in-memory, so it does not hold across
    multiple workers (needs Redis);
  * there is no refresh-token rotation, no MFA, no account lockout;
  * every synthetic rep shares a seeded demo password.
What IS real: bcrypt verification, signed short-lived tokens, httpOnly cookies,
constant-ish-time failure paths, and no user enumeration.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

import bcrypt
import jwt

from ..config import settings

ALGORITHM = settings.jwt_algorithm


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str | None) -> bool:
    """False for a missing hash, but only after doing the work.

    A rep row with no password_hash must not return faster than a wrong
    password, or response timing reveals which accounts exist. So we verify
    against a fixed dummy hash instead of returning early.
    """
    if not hashed:
        bcrypt.checkpw(plain.encode(), _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        # Malformed hash in the database — treat as no match, never as a pass.
        return False


# Cost must match what hash_password produces, or the timing defence is useless.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt())


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenClaims:
    chair_id: int
    rep_code: int
    rep_name: str


def issue_token(*, chair_id: int, rep_code: int, rep_name: str) -> str:
    now = int(time.time())
    payload = {
        "sub": str(rep_code),
        "chair_id": chair_id,
        "rep_code": rep_code,
        "rep_name": rep_name,
        "iat": now,
        "exp": now + settings.jwt_ttl_hours * 3600,
        "iss": "qorvexa-mr-assistant",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


class InvalidToken(Exception):
    pass


def decode_token(token: str) -> TokenClaims:
    """Verifies signature AND expiry, then returns only the claims we trust.

    chair_id is read from here and passed into RepContext. It is never taken
    from a request body, query parameter or header — that is the invariant the
    tool layer depends on.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            issuer="qorvexa-mr-assistant",
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc

    try:
        return TokenClaims(
            chair_id=int(payload["chair_id"]),
            rep_code=int(payload["rep_code"]),
            rep_name=str(payload.get("rep_name") or payload["sub"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidToken(f"malformed claims: {exc}") from exc


# ---------------------------------------------------------------------------
# login rate limiting
# ---------------------------------------------------------------------------

class RateLimiter:
    """Sliding window, per key. In-process only — see the module docstring."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        """True if the attempt is allowed. Records it as a side effect."""
        now = time.monotonic()
        window = self._hits[key]
        cutoff = now - self.window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.max_attempts:
            return False
        window.append(now)
        return True

    def reset(self, key: str) -> None:
        """Called after a successful login, so one typo does not count against
        a legitimate rep for the rest of the window."""
        self._hits.pop(key, None)

    def retry_after(self, key: str) -> int:
        window = self._hits.get(key)
        if not window:
            return 0
        return max(0, int(self.window_seconds - (time.monotonic() - window[0])) + 1)


# Keyed on the client IP. With --proxy-headers this is the real caller; without
# it (i.e. behind a proxy that isn't trusted) it collapses to the proxy's
# address, which is exactly why the per-identifier limiter below exists too.
login_limiter = RateLimiter(settings.login_max_attempts, settings.login_window_seconds)

# Keyed on the login identifier (rep_code / chair_id). This is the limiter that
# actually stops per-account brute force and — crucially — does NOT lock out the
# whole field force when many reps share one proxy IP. A little more generous
# than the IP bucket, since a legitimate rep retrying their own code is common.
login_identifier_limiter = RateLimiter(
    settings.login_max_attempts * 2, settings.login_window_seconds
)
