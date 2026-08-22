"""The numeric grounding check.

The regression cases at the bottom are taken from real logged answers that the
check flagged wrongly — floats that Postgres returns as `95.0` and the model
writes as `95%`. It fired on 2 of 6 consecutive correct answers before the fix.
"""

from __future__ import annotations

from app.bot.guardrails import check_grounding, extract_numeric_claims


def test_matching_number_is_grounded():
    assert check_grounding("You made 247 visits.", '{"count": 247}')["grounded"]


def test_invented_number_is_flagged():
    verdict = check_grounding("You made 999 visits.", '{"count": 247}')
    assert not verdict["grounded"]
    assert "999" in verdict["unverified_claims"]


def test_currency_and_thousands_separators_normalise():
    assert check_grounding("RCPA of Rs. 5,560", '{"rcpa": 5560}')["grounded"]
    assert check_grounding("RCPA of 5560", '{"rcpa": "Rs. 5,560"}')["grounded"]


def test_single_digits_are_ignored():
    """Too many false positives from ordinary prose ("1 sample", "2 brands")."""
    assert extract_numeric_claims("visit 1 doctor") == []


def test_percentage_matches_a_plain_number():
    assert check_grounding("coverage is 79%", '{"mcr_coverage": 79}')["grounded"]


# --- regression: floats returned as N.0 by the database ---------------------

def test_trailing_zero_float_matches_the_integer_form():
    """Postgres returns DOUBLE as 95.0; the model writes 95%. Same number."""
    assert check_grounding("95% target", '{"mcr_threshold": 95.0}')["grounded"]
    assert check_grounding("MCR count: 38", '{"mcr_count": 38.0}')["grounded"]
    assert check_grounding("declined from 67 to 62", '{"pm": 67.0, "cm": 62.0}')["grounded"]


def test_multiple_zero_decimals_also_match():
    assert check_grounding("95 percent", '{"t": 95.00}')["grounded"]


def test_a_real_decimal_is_still_compared_exactly():
    """The fix must not make 95.5 and 95 equal."""
    verdict = check_grounding("coverage 95.5%", '{"mcr_coverage": 95.0}')
    assert not verdict["grounded"]
    assert "95.5" in verdict["unverified_claims"]


def test_invented_dosage_is_still_caught():
    """A true positive the fix must preserve.

    The model wrote "Osteovim 10 mg" when the data has only the brand name. That
    dosage is invented and should be flagged.
    """
    verdict = check_grounding(
        "Discuss Osteovim 10 mg samples.", '{"brands": [{"brand_name": "Osteovim"}]}'
    )
    assert not verdict["grounded"]
    assert "10" in verdict["unverified_claims"]


# --- regression: the model rounding a float for presentation ----------------

def test_rounded_currency_matches_the_full_precision_source():
    """Real case: the database holds 29974.8427; the model wrote ₹29,974.84."""
    assert check_grounding(
        "average 3-month RCPA is ₹29,974.84",
        '{"avg_3_month_rcpa": 29974.8427}',
    )["grounded"]


def test_truncated_form_also_matches():
    assert check_grounding("about 29974.8", '{"v": 29974.8427}')["grounded"]
    assert check_grounding("about 29975", '{"v": 29974.8427}')["grounded"]


def test_rounding_does_not_launder_a_different_number():
    """The tolerance must not accept a number that simply isn't there."""
    verdict = check_grounding("₹31,000.00", '{"avg_3_month_rcpa": 29974.8427}')
    assert not verdict["grounded"]
    assert "31000" in verdict["unverified_claims"]


def test_duplicate_unverified_claims_are_reported_once():
    verdict = check_grounding("999 and again 999", '{"n": 1}')
    assert verdict["unverified_claims"] == ["999"]


def test_answer_with_no_numbers_is_grounded():
    assert check_grounding("I could not find that doctor.", "{}")["grounded"]
