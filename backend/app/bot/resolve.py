"""Doctor name -> doctor_id resolution, scoped to one rep's own chair.

Ambiguity is returned, never guessed: a name that doesn't match uniquely comes
back as a list of candidates for the agent to disambiguate with the rep, rather
than silently picking the top fuzzy match and briefing on the wrong doctor.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz, process

FUZZY_MATCH_THRESHOLD = 65  # rapidfuzz score (0-100); below this, treat as no match
MAX_CANDIDATES = 5

_COLUMNS = ["doctor_id", "doctor_name", "name_norm", "specialty", "clinic_name", "city_name"]


def normalize_name(name: str) -> str:
    """Mirrors NAME_NORM_SQL in etl/load_postgres.py, which builds doctors.name_norm.

    These two must stay in sync — the fuzzy matcher compares this Python
    normalisation against the value the ETL stored. If you change one, change
    both (noted in CLAUDE.md).
    """
    name = name.strip()
    name = re.sub(r"^(DR\.?|DOCTOR)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name)
    return name.upper()


def find_doctor_candidates(conn, chair_id: int, query_name: str) -> list[dict]:
    """Candidate doctors for `query_name` within `chair_id`'s book.

    A single exact match means the name resolved uniquely. Multiple results
    (exact duplicates or fuzzy matches) mean the caller must ask the rep which
    one they mean before proceeding.

    Note this reads the base `doctors` table directly, not the `my_doctors`
    scoped CTE — correct, because the denylist governs run_sql only, and the
    chair_id filter here is bound, not interpolated.
    """
    query_norm = normalize_name(query_name)

    rows = conn.execute(
        """
        SELECT doctor_id, doctor_name, name_norm,
               COALESCE(specialty, '')   AS specialty,
               COALESCE(clinic_name, '') AS clinic_name,
               COALESCE(city_name, '')   AS city_name
        FROM doctors
        WHERE chair_id = %s
        """,
        (chair_id,),
    ).fetchall()
    if not rows:
        return []

    # A large share of doctor_name (and therefore name_norm) values in this data
    # are NULL — those rows can never be matched by name and would otherwise
    # crash rapidfuzz, which does not accept None as a choice.
    doctors = [dict(zip(_COLUMNS, row, strict=True)) for row in rows if row[2] is not None]
    if not doctors:
        return []

    exact = [d for d in doctors if d["name_norm"] == query_norm]
    if exact:
        return [_to_candidate(d, match_score=100) for d in exact]

    choices = {i: d["name_norm"] for i, d in enumerate(doctors)}
    matches = process.extract(
        query_norm,
        choices,
        scorer=fuzz.WRatio,
        limit=MAX_CANDIDATES,
        score_cutoff=FUZZY_MATCH_THRESHOLD,
    )
    return [_to_candidate(doctors[idx], match_score=round(score)) for _, score, idx in matches]


def _to_candidate(doctor: dict, match_score: int) -> dict:
    return {
        "doctor_id": doctor["doctor_id"],
        "doctor_name": doctor["doctor_name"],
        "specialty": doctor["specialty"],
        "clinic_name": doctor["clinic_name"],
        "city_name": doctor["city_name"],
        "match_score": match_score,
    }
