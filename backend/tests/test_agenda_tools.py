"""Mail reaching the model is untrusted text that anyone can send.

A retrieved PDF at least had to be ingested by someone. A mail body arrives
because a stranger knew the rep's address, which makes this the widest
prompt-injection surface in the app — and the one an attacker needs no access to
reach.

The other thing under test is that triage is STRUCTURAL. A vendor newsletter
shouting "URGENT" is not an action and a doctor's quiet question is; scoring the
wording would hand the rep's priority list to anyone who can email them.

The Gmail boundary is an httpx.MockTransport over hand-authored fixtures in
tests/fixtures/gmail/. That is a test double, not a second product backend: there
is no synthetic mailbox in this repository.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.bot.context import RepContext
from app.integrations.google import gmail
from app.services import agenda as agenda_service

ME = "rep@example.test"
CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Test Rep", email_account=ME)
NOW = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "gmail" / "threads.json").read_text())


def _thread(name: str) -> gmail.Thread:
    return gmail.parse_thread(FIXTURES[name], me=ME)


def _classify(name: str):
    return agenda_service.classify(_thread(name), me=ME, now=NOW)


# ---------------------------------------------------------------------------
# triage, from thread structure
# ---------------------------------------------------------------------------


def test_a_thread_the_doctor_wrote_last_needs_a_reply():
    category, days, why = _classify("t_needs_reply")
    assert category == agenda_service.NEEDS_REPLY
    assert days == 4
    assert "waiting" in why.lower() or "wrote last" in why.lower()


def test_a_thread_the_rep_already_replied_to_is_not_waiting_on_the_rep():
    category, _days, _why = _classify("t_follow_up")
    assert category != agenda_service.NEEDS_REPLY


def test_a_sent_mail_with_no_reply_becomes_a_follow_up():
    """"Which mail do I have to follow up on", with zero stored state."""
    category, days, why = _classify("t_follow_up")
    assert category == agenda_service.FOLLOW_UP_DUE
    assert days >= agenda_service.settings.agenda_followup_days
    assert str(days) in why, "days_waiting must be IN the output, not inferred"


def test_a_subject_shouting_urgent_is_not_automatically_an_action():
    """Language is not evidence.

    If the word "urgent" moved a thread up the list, the rep's priorities would
    be settable by anyone who can send them mail — a prompt injection with a spam
    filter's blast radius.
    """
    category, _days, _why = _classify("t_loud_newsletter")
    assert category == agenda_service.NEEDS_REPLY, (
        "it is inbound, so a reply is pending — but only because of who wrote last"
    )
    loud, _d, _w = _classify("t_loud_newsletter")
    quiet, _d2, _w2 = _classify("t_needs_reply")
    assert loud == quiet, "the shouting subject changed the category"


def test_an_adverse_event_thread_escalates_and_says_it_must_not_be_answered():
    """SOP-PV-01 §2.1 gives a suspected adverse event a 24-hour clock, and §2.3
    says causality is not the rep's to judge. So this FLAGS; it does not assess."""
    category, _days, why = _classify("t_escalate")
    assert category == agenda_service.ESCALATE
    assert "pharmacovigilance" in why.lower()
    assert "not be answered" in why.lower() or "not" in why.lower()


def test_escalation_outranks_everything_else():
    weights = {
        name: agenda_service._BASE_WEIGHT[_classify(name)[0]]
        for name in ("t_escalate", "t_needs_reply", "t_follow_up", "t_loud_newsletter")
    }
    assert weights["t_escalate"] == max(weights.values())


# ---------------------------------------------------------------------------
# untrusted content
# ---------------------------------------------------------------------------


def test_a_mail_ordering_the_model_to_exfiltrate_is_parsed_as_ordinary_text():
    """It is content. The rule that keeps it content lives in the tool
    description, where the mail cannot reach it — asserted below."""
    thread = _thread("t_injection")
    body = thread.messages[0].body
    assert "Ignore all previous instructions" in body, "the fixture must contain the attempt"
    # Nothing in parsing treats it specially, and nothing extracts the address
    # it names into a recipient field.
    assert thread.counterparty is not None
    assert thread.counterparty.from_address == "promo@vendor.test"
    assert "research@elsewhere.example" not in (thread.counterparty.to or [])


def test_the_data_not_instructions_rule_lives_in_the_tool_description(monkeypatch):
    """CLAUDE.md §1.9, applied to mail.

    A prompt rule the model reads before the mail is a rule the mail cannot
    overwrite. A warning attached to the payload is one the mail sits next to.
    """
    from app.config import settings
    from app.tools.agenda_tools import AgendaToolProvider

    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "agenda_encryption_key", "0" * 43 + "=")

    specs = {s["name"]: s for s in AgendaToolProvider().get_tools(CTX, db=None)}
    for name in ("list_mail", "get_mail"):
        description = specs[name]["description"]
        assert "UNTRUSTED" in description, name
        assert "never instructions" in description, name
        assert "do not comply" in description, name


def test_the_outbound_rules_are_in_the_send_tools_description(monkeypatch):
    from app.config import settings
    from app.tools.agenda_tools import AgendaToolProvider

    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "agenda_encryption_key", "0" * 43 + "=")

    spec = next(
        s for s in AgendaToolProvider().get_tools(CTX, db=None) if s["name"] == "send_email"
    )
    assert "REQUIRES HUMAN APPROVAL" in spec["description"]
    assert "pharmacovigilance" in spec["description"]
    assert "citation" in spec["description"]
    # And the model is told the recipient is not its to choose.
    assert "taken from" in spec["description"] and "thread" in spec["description"]


# ---------------------------------------------------------------------------
# the recipient rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replying_takes_the_recipient_from_the_thread_and_ignores_the_model(monkeypatch):
    """The control that does not depend on the model behaving.

    A mail body can ask for a copy to go anywhere. On a reply the address is not
    validated against the model's suggestion — the suggestion is discarded.
    """
    conn = agenda_service.Connection(
        chair_id=CTX.chair_id,
        rep_code=CTX.rep_code,
        email_account=ME,
        scopes=("https://www.googleapis.com/auth/gmail.send",),
        calendar_tz="UTC",
    )

    async def fake_token(_ctx):
        return "token", conn

    async def fake_get_thread(_token, *, thread_id, metadata_only=False):
        del metadata_only
        return FIXTURES[thread_id]

    monkeypatch.setattr(agenda_service, "_access_token", fake_token)
    monkeypatch.setattr(agenda_service.gmail, "get_thread", fake_get_thread)

    recipients, source, error = await agenda_service.resolve_recipients(
        CTX, thread_id="t_needs_reply", to="attacker@evil.test"
    )
    assert error is None
    assert source == "thread"
    assert recipients == ["t.saxena@clinic.test"]
    assert "attacker@evil.test" not in recipients


@pytest.mark.asyncio
async def test_a_new_mail_to_a_stranger_is_refused(monkeypatch):
    async def no_correspondents(_ctx):
        return set()

    monkeypatch.setattr(agenda_service, "correspondents", no_correspondents)
    recipients, source, error = await agenda_service.resolve_recipients(
        CTX, thread_id=None, to="research@elsewhere.example"
    )
    assert recipients == []
    assert source == "rejected"
    assert error and "not someone this rep has corresponded with" in error


@pytest.mark.asyncio
async def test_a_new_mail_to_a_known_correspondent_is_allowed(monkeypatch):
    async def known(_ctx):
        return {"t.saxena@clinic.test"}

    monkeypatch.setattr(agenda_service, "correspondents", known)
    recipients, source, error = await agenda_service.resolve_recipients(
        CTX, thread_id=None, to="T.Saxena@Clinic.Test"
    )
    assert error is None
    assert source == "allowlisted"
    assert recipients == ["t.saxena@clinic.test"], "the address should be normalised"


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_a_thread_is_ordered_oldest_first_and_knows_which_side_is_the_rep():
    thread = _thread("t_follow_up")
    assert [m.outbound for m in thread.messages] == [False, True]
    assert thread.messages[0].date < thread.messages[1].date
    assert thread.counterparty is not None
    assert thread.counterparty.from_address == "k.saxena@clinic.test"


def test_a_body_longer_than_the_cap_is_truncated():
    """The model pays for every character, and a monograph-length reply adds
    nothing a rep needs for triage."""
    import base64

    long_body = "x" * (gmail.MAX_BODY_CHARS + 500)
    raw = {
        "id": "t_long",
        "messages": [
            {
                "id": "m",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": "a@b.test"},
                        {"name": "Date", "value": "Mon, 18 Aug 2026 09:14:00 +0530"},
                        {"name": "Subject", "value": "long"},
                    ],
                    "body": {"data": base64.urlsafe_b64encode(long_body.encode()).decode()},
                },
            }
        ],
    }
    thread = gmail.parse_thread(raw, me=ME)
    assert len(thread.messages[0].body) == gmail.MAX_BODY_CHARS
