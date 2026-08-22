"""The run_sql scoping layer, tested as pure functions against the manifest.

No database: these assert on generated SQL text, which is where the PII and
tenant-isolation decisions actually live.
"""

from __future__ import annotations

import re

from app.bot import schema
from app.tools.sql_tools import (
    FORBIDDEN_SQL_KEYWORDS,
    _base_relations_pattern,
    build_scoped_query,
    scoped_ctes,
)

CHAIR = 7100001


def test_pii_columns_never_appear_in_any_cte():
    """The `SELECT *` leak, closed at the source.

    The PII guard is a regex over the model's query text, so `SELECT * FROM
    my_doctors` would never type `mobile` and would return it anyway. The CTEs
    therefore enumerate non-PII columns explicitly.
    """
    pii = schema.pii_columns()
    assert pii, "expected at least one PII column in the manifest"
    all_sql = " ".join(scoped_ctes(CHAIR).values())
    for column in pii:
        assert not re.search(rf"\b{column}\b", all_sql), column


def test_no_cte_uses_select_star():
    for alias, sql in scoped_ctes(CHAIR).items():
        assert "*" not in sql, alias


def test_every_chair_scoped_cte_filters_on_this_chair():
    kinds = schema.scope_kinds()
    for alias, sql in scoped_ctes(CHAIR).items():
        relation = alias.removeprefix("my_")
        if kinds.get(relation, "chair") == "chair":
            assert f"chair_id = {CHAIR}" in sql, alias


def test_doctor_scoped_cte_goes_through_the_reps_doctors():
    sql = scoped_ctes(CHAIR)["my_doctor_codes"]
    # doctor_codes has no chair_id of its own, so it must be reached via doctors.
    assert "doctor_id IN (SELECT doctor_id FROM doctors WHERE chair_id" in sql
    assert str(CHAIR) in sql


def test_scoped_query_splices_into_an_existing_with_clause():
    out = build_scoped_query("WITH x AS (SELECT 1) SELECT * FROM x", CHAIR)
    assert out.upper().startswith("WITH ")
    # Exactly one WITH keyword: ours absorbed theirs.
    assert len(re.findall(r"\bWITH\b", out, re.IGNORECASE)) == 1


def test_base_relations_pattern_matches_bare_names_but_not_my_aliases():
    pattern = _base_relations_pattern()
    assert pattern.search("SELECT * FROM doctors")
    assert pattern.search("select 1 from VISITS")
    # `my_doctors` contains no standalone `doctors` — underscore is a word char.
    assert not pattern.search("SELECT * FROM my_doctors")
    assert not pattern.search("SELECT * FROM my_visits JOIN my_brands USING (doctor_id)")


def test_forbidden_keywords_cover_the_write_and_escape_verbs():
    for statement in [
        "insert into doctors values (1)",
        "UPDATE doctors SET x = 1",
        "delete from doctors",
        "drop table doctors",
        "copy doctors to '/tmp/x'",
        "grant select on doctors to public",
        "set role postgres",
    ]:
        assert FORBIDDEN_SQL_KEYWORDS.search(statement), statement


def test_ordinary_select_is_not_falsely_rejected():
    assert not FORBIDDEN_SQL_KEYWORDS.search(
        "SELECT specialty, COUNT(*) AS n FROM my_doctors GROUP BY specialty ORDER BY n DESC"
    )
