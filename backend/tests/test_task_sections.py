"""Task sectioning as a table, and the mail-search injection boundary.

Both are pure functions with no database and no network, which is why they get a
table rather than a fixture: the interesting cases are boundaries in time and in
string escaping, and both are cheap to enumerate exhaustively.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.integrations.google.gmail import search_query
from app.services.agenda import DONE, OVERDUE, SOMEDAY, TODAY, UPCOMING, task_section

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")

#: 10:00 on 2026-08-24, the reference "now" for the table below.
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
TODAY_D = date(2026, 8, 24)


@pytest.mark.parametrize(
    ("label", "due_date", "due_time", "done_at", "expected"),
    [
        ("due yesterday", date(2026, 8, 23), None, None, OVERDUE),
        ("due today, all day", TODAY_D, None, None, TODAY),
        # THE case worth pinning. "Overdue" means the due MOMENT has passed, not
        # that the date is strictly in the past. The other reading quietly tells
        # a rep they are fine while they are already late.
        ("due today 09:00, read at 10:00", TODAY_D, time(9, 0), None, OVERDUE),
        ("due today 10:00, read at 10:00", TODAY_D, time(10, 0), None, OVERDUE),
        ("due today 17:00, read at 10:00", TODAY_D, time(17, 0), None, TODAY),
        ("due tomorrow", date(2026, 8, 25), None, None, UPCOMING),
        ("due tomorrow, early", date(2026, 8, 25), time(0, 1), None, UPCOMING),
        ("no due date", None, None, None, SOMEDAY),
        # Done beats everything, including long overdue: a completed task is not
        # a thing the rep is behind on.
        ("done and overdue", date(2026, 1, 1), None, NOW, DONE),
        ("done with no date", None, None, NOW, DONE),
    ],
)
def test_the_section_table(label, due_date, due_time, done_at, expected):
    got = task_section(due_date=due_date, due_time=due_time, done_at=done_at, now=NOW)
    assert got == expected, f"{label}: expected {expected}, got {got}"


def test_the_same_task_sections_differently_in_two_timezones():
    """The zone is not decoration.

    At 01:59 IST on the 24th it is still 20:29 UTC on the 23rd. A task due on the
    24th is therefore "today" in Kolkata and "upcoming" in UTC — both correct, and
    the reason AGENDA_TIMEZONE exists rather than being guessed. Get it wrong and
    a rep's list is shifted by a day: quiet wrongness, not a crash.
    """
    ist_now = datetime(2026, 8, 24, 1, 59, tzinfo=IST)
    utc_now = ist_now.astimezone(UTC)
    assert utc_now.date() == date(2026, 8, 23)

    in_ist = task_section(due_date=TODAY_D, due_time=None, done_at=None, now=ist_now)
    in_utc = task_section(due_date=TODAY_D, due_time=None, done_at=None, now=utc_now)
    assert in_ist == TODAY
    assert in_utc == UPCOMING


# ---------------------------------------------------------------------------
# the mail-search injection boundary
# ---------------------------------------------------------------------------


def test_a_quote_cannot_close_the_quoted_term():
    """THE injection test for this feature.

    Quoting is the defence, and stripping quotes first is what makes the quoting
    sound. Unstripped, `x" OR from:ceo` would close the quote we added and
    everything after it would be read by Gmail as a new operator — the quoting
    would look like a control while providing none.

    This matters because the values arrive from a model whose context contains
    mail bodies written by anyone who can email the rep.
    """
    q = search_query(subject_contains='x" OR from:ceo@corp.test')
    assert q.startswith('subject:"x OR from:ceo@corp.test"')
    # One quoted term: exactly the opening and closing quote we put there.
    assert q.count('"') == 2

    # Asserted POSITIONALLY, not with `" from:" not in q`. That ban fails on the
    # correct output, because `from:` does appear — INSIDE the quotes, where Gmail
    # reads it as text. A substring ban cannot tell a smuggled operator from one
    # harmlessly quoted, which is the same mistake as banning "superior to" to
    # test a compliance rule.
    assert _outside_quotes(q) == " -in:spam -in:trash"


def _outside_quotes(query: str) -> str:
    """Everything Gmail will read as SYNTAX: the query with quoted spans and their
    leading operator removed. What is left must be only what we intended."""
    out, in_quotes, pending = [], False, []
    for ch in query:
        if ch == '"':
            if in_quotes:
                pending.clear()  # drop the operator that introduced this span
            in_quotes = not in_quotes
            continue
        if in_quotes:
            continue
        if ch == " ":
            out.extend(pending)
            pending.clear()
            out.append(ch)
        else:
            pending.append(ch)
    out.extend(pending)
    return "".join(out)


def test_the_outside_quotes_helper_catches_a_real_escape():
    """The helper is the assertion, so it gets its own check: an unquoted operator
    must show up in what it returns."""
    assert "from:" in _outside_quotes('subject:"x" OR from:ceo')
    assert "from:" not in _outside_quotes('subject:"x OR from:ceo"')


def test_control_characters_cannot_break_the_query():
    q = search_query(from_name='a\nfrom:b\r\\c')
    assert "\n" not in q and "\r" not in q and "\\" not in q
    assert q.startswith('from:"afrom:bc"')


def test_since_days_is_clamped_and_never_carries_text():
    """The one unquoted field, so it must not be able to carry characters."""
    assert "newer_than:365d" in search_query(from_name="x", since_days=99_999)
    assert "newer_than:1d" in search_query(from_name="x", since_days=-5)
    assert "newer_than" not in search_query(from_name="x", since_days=None)


def test_spam_and_trash_are_always_excluded():
    """Spam is not the rep's agenda, and a search that surfaces it hands anyone
    who can email them a way onto their screen."""
    q = search_query(from_name="anyone")
    assert "-in:spam" in q and "-in:trash" in q


def test_an_empty_filter_contributes_no_operator():
    q = search_query(from_name="   ", subject_contains="")
    assert q == "-in:spam -in:trash"
