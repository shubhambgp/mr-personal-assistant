"""A dead Google grant must announce itself, and must not take others with it.

Before this existed, an expired connection reported itself as CONNECTED forever:
Settings showed a green badge, and every mail tool returned a bare "Google
returned 400." That is worse than a connection that says it is broken, because
the rep has no idea what to do about it.

In External+Testing audience — the only one available without Google's CASA
assessment — a refresh token expires after SEVEN DAYS, so this path is not an
edge case. The same handling covers a rep revoking access or changing password.

No database and no network: the two collaborators (the connection row and the
token refresh) are injected.
"""

from __future__ import annotations

import pytest

from app.bot.context import RepContext
from app.integrations.google.client import GoogleError
from app.services import agenda as agenda_service

CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Test Rep")

LIVE = agenda_service.Connection(
    chair_id=7100001,
    rep_code=7800001,
    email_account="rep@example.test",
    scopes=("https://www.googleapis.com/auth/gmail.readonly",),
    calendar_tz="Asia/Kolkata",
)
STALE = agenda_service.Connection(**{**LIVE.__dict__, "stale": True})


@pytest.fixture
def marked(monkeypatch) -> list[int]:
    """Records which chair_ids mark_stale() was called for."""
    calls: list[int] = []
    monkeypatch.setattr(agenda_service, "mark_stale", calls.append)
    return calls


def _refresh_raises(monkeypatch, code: str | None):
    async def boom(_token: str) -> dict:
        raise GoogleError("Google returned 400.", status=400, code=code)

    monkeypatch.setattr(agenda_service.oauth, "refresh_access_token", boom)


def _stored(monkeypatch, row: agenda_service.Connection | None, token: str | None = "sealed"):
    monkeypatch.setattr(agenda_service, "connection", lambda *_a, **_k: row)
    monkeypatch.setattr(agenda_service, "cached_access_token", lambda _c: None)
    monkeypatch.setattr(agenda_service, "open_sealed", lambda _v: "refresh-token")

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_a, **_k):
            return None

        def fetchone(self):
            return {"refresh_token_enc": token} if token else None

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self, **_k):
            return _Cur()

    class _Pool:
        def connection(self):
            return _Conn()

    monkeypatch.setattr(agenda_service.db, "rw_pool", lambda: _Pool())


@pytest.mark.asyncio
async def test_invalid_grant_marks_the_connection_stale_and_raises_expired(monkeypatch, marked):
    _stored(monkeypatch, LIVE)
    _refresh_raises(monkeypatch, "invalid_grant")

    with pytest.raises(agenda_service.ConnectionExpired):
        await agenda_service._access_token(CTX)
    assert marked == [CTX.chair_id]


@pytest.mark.asyncio
async def test_invalid_client_leaves_every_token_alone(monkeypatch, marked):
    """The regression test for a deployment-wide outage.

    invalid_client means the OPERATOR's secret is wrong, not that this rep's grant
    died. A naive "400 means dead" would delete all 25 reps' credentials on the
    first request after a bad deploy, and consent cannot be restored server-side —
    every rep would have to reconnect by hand.
    """
    _stored(monkeypatch, LIVE)
    _refresh_raises(monkeypatch, "invalid_client")

    with pytest.raises(GoogleError) as caught:
        await agenda_service._access_token(CTX)
    assert not isinstance(caught.value, agenda_service.ConnectionExpired)
    assert marked == []


@pytest.mark.asyncio
async def test_an_unknown_error_also_leaves_tokens_alone(monkeypatch, marked):
    """Fail closed on the DESTRUCTIVE action: only a code we recognise deletes."""
    _stored(monkeypatch, LIVE)
    _refresh_raises(monkeypatch, None)

    with pytest.raises(GoogleError):
        await agenda_service._access_token(CTX)
    assert marked == []


@pytest.mark.asyncio
async def test_an_already_stale_row_refuses_without_calling_google(monkeypatch, marked):
    """No credential means nothing to try. Proven by the absence of the call."""
    _stored(monkeypatch, STALE)

    async def must_not_run(_token: str) -> dict:
        raise AssertionError("refresh must not be attempted for a stale connection")

    monkeypatch.setattr(agenda_service.oauth, "refresh_access_token", must_not_run)
    with pytest.raises(agenda_service.ConnectionExpired):
        await agenda_service._access_token(CTX)
    assert marked == []


def test_the_two_exceptions_carry_different_guidance():
    """"You never connected" and "the account you connected stopped working" are
    different facts. Telling a rep the first when the second is true sends them to
    set up something they already set up."""
    assert "expired" in agenda_service.ConnectionExpired.guidance.lower()
    assert "expired" not in agenda_service.NotConnected.guidance.lower()
    # Inheritance is what covers the handlers that never name the expired case.
    assert issubclass(agenda_service.ConnectionExpired, agenda_service.NotConnected)


def test_a_stale_connection_hides_the_mail_tools(monkeypatch):
    """deps.agenda_rep leaves email_account None for a stale row, so get_tools
    contributes only what still works. Offering a tool that is CERTAIN to fail is
    worse than not offering it."""
    import base64
    import dataclasses

    from app.config import settings
    from app.tools.agenda_tools import AgendaToolProvider

    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(
        settings, "agenda_encryption_key", base64.urlsafe_b64encode(b"\x01" * 32).decode()
    )

    stale_ctx = dataclasses.replace(CTX, email_account=None)
    names = {t["name"] for t in AgendaToolProvider().get_tools(stale_ctx, conn=None)}
    assert "list_mail" not in names
    assert "send_email" not in names
    # ...but the rep can still be told what happened, and tasks still work.
    assert "agenda_status" in names
    assert "create_task" in names
