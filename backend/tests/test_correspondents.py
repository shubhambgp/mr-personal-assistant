"""The correspondent allowlist covers BOTH directions of the rep's own mail.

The bug: the allowlist was built from `TriageItem.from_address`, which is the
COUNTERPARTY's address — so a thread the rep WROTE that nobody answered has no
counterparty, contributes an empty string, and is dropped. "An address the rep
has already corresponded with" therefore meant "somebody who wrote to me", and a
rep could not follow up on their own outbound mail. Measured on a real mailbox: 5
of 8 threads in the window were the rep writing with no reply, and the address
they were trying to write to was one of them.

Asserted as a mechanism: an outbound-only thread must put its recipient in the
set, and an address in no thread at all must stay out — the second half is the
security property (CLAUDE.md §1.10) and must not be relaxed by the first.
"""

from __future__ import annotations

import pytest

from app.bot.context import RepContext
from app.services import agenda as agenda_service

pytestmark = pytest.mark.asyncio

ME = "rep@example.test"
CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Test Rep", email_account=ME)

CONNECTION = agenda_service.Connection(
    chair_id=7100001,
    rep_code=7800001,
    email_account=ME,
    scopes=("https://www.googleapis.com/auth/gmail.readonly",),
    calendar_tz="UTC",
)

#: Two threads. The first is the rep writing to someone who never replied — the
#: shape that used to be invisible. The second is an ordinary inbound thread.
THREADS = {
    "t-outbound-only": {
        "id": "t-outbound-only",
        "messages": [
            {
                "id": "m1",
                "payload": {
                    "headers": [
                        {"name": "From", "value": f"Rep <{ME}>"},
                        {"name": "To", "value": "Dr Iyer <dr.iyer@example.test>"},
                        {"name": "Subject", "value": "Following up"},
                        {"name": "Date", "value": "Mon, 4 Aug 2025 10:00:00 +0530"},
                    ]
                },
            }
        ],
    },
    "t-inbound": {
        "id": "t-inbound",
        "messages": [
            {
                "id": "m2",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Dr Sharma <dr.sharma@example.test>"},
                        {"name": "To", "value": ME},
                        {"name": "Subject", "value": "Question"},
                        {"name": "Date", "value": "Tue, 5 Aug 2025 10:00:00 +0530"},
                    ]
                },
            }
        ],
    },
}


@pytest.fixture
def mailbox(monkeypatch):
    """A two-thread mailbox, with no Google and no database behind it."""
    agenda_service._correspondents_cache.clear()

    async def token(_ctx):
        return "tok", CONNECTION

    async def list_thread_ids(_token, *, query, limit):
        # The window/exclusions the allowlist asks for, kept honest.
        assert "newer_than:" in query and "-in:spam" in query
        assert limit == agenda_service.CORRESPONDENT_THREAD_CAP
        return list(THREADS)

    async def get_thread(_token, *, thread_id, metadata_only=False):
        assert metadata_only, "the allowlist needs headers only, never bodies"
        return THREADS[thread_id]

    monkeypatch.setattr(agenda_service, "_access_token", token)
    monkeypatch.setattr(agenda_service.gmail, "list_thread_ids", list_thread_ids)
    monkeypatch.setattr(agenda_service.gmail, "get_thread", get_thread)


async def test_someone_the_rep_wrote_to_is_a_correspondent(mailbox):
    known = await agenda_service.correspondents(CTX)
    assert "dr.iyer@example.test" in known, (
        "an address the rep SENT to was not a correspondent — the outbound half "
        "of the allowlist is missing again"
    )


async def test_someone_who_wrote_to_the_rep_is_still_a_correspondent(mailbox):
    assert "dr.sharma@example.test" in await agenda_service.correspondents(CTX)


async def test_an_address_in_no_thread_is_not_a_correspondent(mailbox):
    """The security property. Broadening to both directions must not become
    'anything goes' — an address a mail body asked us to write to appears in no
    thread of the rep's own, so it stays out."""
    known = await agenda_service.correspondents(CTX)
    assert "research@elsewhere.example" not in known

    recipients, source, error = await agenda_service.resolve_recipients(
        CTX, thread_id=None, to="research@elsewhere.example"
    )
    assert recipients == [] and source == "rejected" and error


async def test_the_allowlist_is_cached_per_rep(mailbox, monkeypatch):
    """One send must not re-read the mailbox once per attempt."""
    calls = {"n": 0}
    original = agenda_service.gmail.list_thread_ids

    async def counted(*args, **kwargs):
        calls["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(agenda_service.gmail, "list_thread_ids", counted)
    await agenda_service.correspondents(CTX)
    await agenda_service.correspondents(CTX)
    assert calls["n"] == 1


async def test_one_unreadable_thread_does_not_empty_the_allowlist(mailbox, monkeypatch):
    """Failing closed here would block every send on a single Gmail hiccup."""
    from app.integrations.google.client import GoogleError

    async def flaky(_token, *, thread_id, metadata_only=False):
        if thread_id == "t-inbound":
            raise GoogleError("boom", status=500)
        return THREADS[thread_id]

    agenda_service._correspondents_cache.clear()
    monkeypatch.setattr(agenda_service.gmail, "get_thread", flaky)
    known = await agenda_service.correspondents(CTX)
    assert known == {"dr.iyer@example.test"}
