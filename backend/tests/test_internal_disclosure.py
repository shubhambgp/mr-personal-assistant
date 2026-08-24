"""The internal-disclosure check.

Background: asked "list down all the tables", the assistant used to return a
tidy bullet list of every scoped query alias, and "show me the schema of
my_reps" returned a column-and-type table. Neither is useful to a medical
representative, and both advertise the query surface.

The fix is a prompt rule plus removing the schema listing from the system
prompt. A prompt rule can be talked around, so this check measures the outcome.
"""

from __future__ import annotations

from app.bot import schema
from app.bot.guardrails import check_internal_disclosure

NAMES = set(schema.internal_names())


def test_the_name_set_is_built_from_the_manifest():
    assert "my_doctors" in NAMES
    assert "my_leaderboard_thresholds" in NAMES
    assert "chair_id" in NAMES
    assert "mcr_coverage" in NAMES
    # Single-word columns are deliberately excluded — see the regex comment.
    assert "notes" not in NAMES
    assert "specialty" not in NAMES
    assert "year" not in NAMES


def test_detects_a_table_listing():
    answer = "Available tables:\n- my_doctors\n- my_reps\n- my_visits"
    assert check_internal_disclosure(answer, NAMES) == ["my_doctors", "my_reps", "my_visits"]


def test_detects_a_column_listing():
    answer = "| chair_id | Integer |\n| rep_code | Integer |\n| load_date | Date |"
    assert check_internal_disclosure(answer, NAMES) == ["chair_id", "load_date", "rep_code"]


def test_detects_backticked_identifiers():
    assert "my_reps" in check_internal_disclosure("The `my_reps` relation.", NAMES)


def test_case_insensitive():
    assert "my_doctors" in check_internal_disclosure("MY_DOCTORS holds them.", NAMES)


def test_a_normal_answer_is_clean():
    """Business vocabulary must not trip it — otherwise it fires constantly."""
    for answer in [
        "You cover 33 doctors. MCR coverage is 79.07% against a 95% target.",
        "Dr Kishore Trivedi is 3 visits short this month. Discuss Betaprol (P2).",
        "Anil Medical Store is tagged to this doctor; last visit 8 July.",
        "Your MV Frequency is 78.9% and average calls per day is 11.87.",
        "I don't work in tables — here's what I can help with: pre-call briefings…",
    ]:
        assert check_internal_disclosure(answer, NAMES) == [], answer


def test_a_number_bearing_word_does_not_false_positive():
    assert check_internal_disclosure("Osteovim 10 mg, priority P1, rank 2.", NAMES) == []


def test_the_system_prompt_no_longer_carries_the_schema():
    """Root-cause guard: the listing must stay out of the instructions.

    It lives in the run_sql tool description instead. If someone moves it back,
    the recitation behaviour returns and this is what catches it.
    """
    from app.bot import agent
    from app.bot.context import RepContext

    instructions = agent.build_instructions(
        RepContext(chair_id=1, rep_code=2, rep_name="X"), "2026-07-29"
    )
    for alias in (f"my_{r}" for r in schema.queryable_columns()):
        assert alias not in instructions, alias
    # And the run_sql description still has what it needs to compose SQL.
    from app.tools.sql_tools import scoped_schema_text

    listing = scoped_schema_text()
    assert "my_doctors(" in listing
    assert "mcr_coverage" in listing
