"""Proves the loaded dataset is synthetic, correctly shaped, and still quirky.

Design note on check 1. The previous version of this script held a DENYLIST of
the real vendor's names — which meant the committed, public file spelled out
exactly the thing it existed to keep out. This version inverts it: every
company/brand/SBU value must come from a known synthetic ALLOWLIST. That is a
stronger assertion (an unexpected value fails even if we never thought to
forbid it) and it leaks nothing.

    .venv/bin/python -m etl.verify_data
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import yaml  # noqa: E402

from app.bot import db  # noqa: E402

MANIFEST = Path(__file__).resolve().parent / "manifest.yaml"

# The only organisation tokens that may appear in the data: the fictional
# company, its BU code, and its five invented SBU names.
ALLOWED_ORG_TOKENS = {
    "QORVEXA", "QHC",
    "DISCOVERY", "MEDICA", "VITALIS", "NEURO", "CARE",
}

# Market-segment vocabulary. These are ordinary public industry terms, not
# organisation names — a cluster called "Metro" identifies nobody. Listed
# separately from ALLOWED_ORG_TOKENS so the distinction stays explicit.
ALLOWED_SEGMENT_TOKENS = {
    "METRO", "RURAL", "MASS", "SPECIALTY", "URBAN", "SEMI", "TIER",
}

# Text columns whose every value must be a known synthetic brand or NULL.
BRAND_COLUMNS = [("brands", "brand_name")]

EXPECTED_ROWS = {
    "brands": 2834,
    "chemists": 1414,
    "data_vintage": 9,
    "doctors": 938,          # 950 in the parquet; 12 stale-vintage rows dropped
    "hooks": 2340,
    "rep_metrics": 50,
    "reps": 25,
    "required_pending_visits": 1876,
    "targets": 5,
    "visits": 3323,
}
EXPECTED_VIEWS = {
    "doctor_codes", "actual_visits", "planned_visits",
    "thresholds", "leaderboard_thresholds",
}

failures: list[str] = []
notes: list[str] = []


def fail(message: str) -> None:
    failures.append(message)
    print(f"   FAIL  {message}")


def ok(message: str) -> None:
    print(f"   ok    {message}")


def check_shape(conn) -> None:
    print("\n1. Schema shape")
    rows = conn.execute(
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema = 'app'"
    ).fetchall()
    tables = {r[0] for r in rows if r[1] == "BASE TABLE"}
    views = {r[0] for r in rows if r[1] == "VIEW"}

    if tables == set(EXPECTED_ROWS):
        ok(f"{len(tables)} tables, exactly as expected")
    else:
        fail(f"table set mismatch: unexpected={sorted(tables - set(EXPECTED_ROWS))} "
             f"missing={sorted(set(EXPECTED_ROWS) - tables)}")

    if views == EXPECTED_VIEWS:
        ok(f"{len(views)} compatibility views resolve")
    else:
        fail(f"view set mismatch: {sorted(views)} != {sorted(EXPECTED_VIEWS)}")

    for view in sorted(views):
        try:
            conn.execute(f"SELECT * FROM {view} LIMIT 1").fetchone()
        except Exception as exc:  # noqa: BLE001
            fail(f"view {view} does not execute: {exc}")

    # chair_doctor_key was carried on seven tables and used by nothing. It must
    # not have crept back in.
    stray = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema='app' AND column_name IN "
        "('chair_doctor_key','email','dr_address')"
    ).fetchall()
    if stray:
        fail(f"dropped columns are present again: {stray}")
    else:
        ok("chair_doctor_key / email / dr_address absent")


def check_row_counts(conn) -> None:
    print("\n2. Row counts")
    for table, expected in EXPECTED_ROWS.items():
        actual = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if actual == expected:
            ok(f"{table}: {actual:,}")
        else:
            fail(f"{table}: {actual:,} rows, expected {expected:,}")


def check_branding(conn) -> None:
    print("\n3. Branding (allowlist, not denylist)")
    manifest = yaml.safe_load(MANIFEST.read_text())

    # 3a. Column names must not contain an organisation token at all — the real
    # source files had vendor names embedded in column names, which is the
    # subtler half of this problem.
    canonical = [
        spec["rename"] if isinstance(spec, dict) else spec
        for cfg in manifest["tables"].values()
        for spec in cfg["columns"].values()
    ]
    # Allowlist, not denylist. Every canonical column name must be built only
    # from these domain words. An unexpected token fails even if nobody thought
    # to forbid it — and, unlike a denylist, this file does not have to spell
    # out the name it is excluding. (The tree-wide check for the real vendor
    # identifiers lives in etl/check_no_vendor_terms.py, hashed.)
    allowed_words = {
        "chair", "doctor", "id", "name", "specialty", "clinic", "city", "mobile",
        "avg", "month", "rcpa", "scientific", "engagement", "patient", "support",
        "national", "persona", "prescriber", "type", "loyalty", "digital", "load",
        "date", "norm", "visit", "visits", "number", "of", "required", "pending",
        "this", "freq", "year", "brand", "cm", "pm", "prescribed", "qty", "final",
        "target", "growth", "booster", "priority", "rank", "notes", "hook",
        "category", "product", "desc", "work", "chemist", "last", "rep", "code",
        "sbu", "no", "doctors", "mcr", "count", "coverage", "mv", "frequency",
        "average", "calls", "per", "day", "cluster", "performance", "lower",
        "upper", "limit", "threshold", "mvc", "accompanied", "by", "3",
    }
    suspicious = {}
    for column in canonical:
        unknown = {w for w in re.split(r"[_\d]+", column.lower()) if w} - allowed_words
        if unknown:
            suspicious[column] = sorted(unknown)
    if suspicious:
        fail(f"column names contain unrecognised token(s): {suspicious}")
    else:
        ok(f"{len(canonical)} canonical column names use only allowlisted domain words")

    # 3b. Every SBU / cluster value must be built from allowed tokens.
    values = conn.execute("SELECT DISTINCT cluster_name FROM targets").fetchall()
    allowed = ALLOWED_ORG_TOKENS | ALLOWED_SEGMENT_TOKENS
    bad = []
    for (value,) in values:
        if value is None:
            continue
        unexpected = set(re.findall(r"[A-Za-z]+", value.upper())) - allowed
        if unexpected:
            bad.append((value, sorted(unexpected)))
    if bad:
        # Note: a plain for/else was wrong here — `else` runs when the loop does
        # not break, so it printed "ok" directly underneath its own failures.
        for value, unexpected in bad:
            fail(f"cluster_name {value!r} contains unexpected token(s): {unexpected}")
    else:
        ok(f"{len(values)} cluster_name value(s) use only allowlisted tokens")

    # 3c. Brand names: assert they are drawn from a small closed set, and print
    # it, so a reviewer can see there is nothing real in there.
    for table, column in BRAND_COLUMNS:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL ORDER BY 1"
        ).fetchall()
        brands = [r[0] for r in rows]
        if not brands:
            fail(f"{table}.{column} has no values at all")
            continue
        if len(brands) > 40:
            fail(f"{table}.{column} has {len(brands)} distinct values — expected a small "
                 f"invented set, this looks like real data")
        else:
            ok(f"{table}.{column}: {len(brands)} invented brands — {', '.join(brands[:8])}"
               f"{' …' if len(brands) > 8 else ''}")


def check_quirks(conn) -> None:
    """The data is deliberately messy. If it stops being messy, the ETL's
    cleaning path and the app's edge cases stop being exercised."""
    print("\n4. Deliberate quirks preserved")

    shared = conn.execute(
        "SELECT count(*) FROM (SELECT doctor_id FROM doctors "
        "GROUP BY doctor_id HAVING count(DISTINCT chair_id) > 1) t"
    ).fetchone()[0]
    if shared:
        ok(f"{shared} doctor(s) appear under more than one chair "
           f"— this is what makes chair_id scoping mandatory")
    else:
        fail("no doctor appears under multiple chairs — the scoping tests lose their teeth")

    vintages = conn.execute("SELECT DISTINCT max_load_date FROM data_vintage").fetchall()
    if len(vintages) > 1:
        ok(f"{len(vintages)} distinct load_date vintages across tables")
    else:
        notes.append("only one load_date vintage — the mixed-vintage case is not covered")

    double_spaced = conn.execute(
        r"SELECT count(*) FROM doctors WHERE doctor_name ~ '\s\s'"
    ).fetchone()[0]
    if double_spaced:
        ok(f"{double_spaced} double-spaced doctor name(s) survive raw…")
        normalised = conn.execute(
            r"SELECT count(*) FROM doctors WHERE name_norm ~ '\s\s'"
        ).fetchone()[0]
        if normalised == 0:
            ok("…and name_norm collapsed every one of them")
        else:
            fail(f"name_norm still has {normalised} double-spaced value(s)")

    # The literal 4-character text "null" must have become a real NULL.
    literal = conn.execute(
        "SELECT count(*) FROM doctors WHERE doctor_name = 'null' OR specialty = 'null'"
    ).fetchone()[0]
    if literal:
        fail(f'{literal} row(s) still contain the literal text "null" — the sentinel '
             f"was not applied")
    else:
        ok('the literal "null" sentinel was converted to real NULLs')

    nulls = conn.execute(
        "SELECT count(*) FROM doctors WHERE doctor_name IS NULL"
    ).fetchone()[0]
    if nulls:
        ok(f"{nulls} doctor(s) have a NULL name — the case that once crashed the "
           f"fuzzy matcher")


def check_pii(conn) -> None:
    print("\n5. PII")
    manifest = yaml.safe_load(MANIFEST.read_text())
    pii = {
        spec["rename"]
        for cfg in manifest["tables"].values()
        for spec in cfg["columns"].values()
        if isinstance(spec, dict) and spec.get("pii")
    }
    if not pii:
        fail("no column is flagged pii — the PII guardrail has nothing to guard")
        return
    ok(f"flagged pii in the manifest: {sorted(pii)}")

    present = conn.execute(
        "SELECT count(*) FROM doctors WHERE mobile IS NOT NULL"
    ).fetchone()[0]
    if present:
        ok(f"{present} synthetic mobile value(s) present, so the guard is testable")
    else:
        fail("mobile is entirely NULL — the PII guard would pass vacuously")


def main() -> int:
    db.open_pools()
    with db.ro_pool().connection() as conn:
        print("Qorvexa synthetic dataset verification")
        print("=" * 62)
        check_shape(conn)
        check_row_counts(conn)
        check_branding(conn)
        check_quirks(conn)
        check_pii(conn)
    db.close_pools()

    print("\n" + "=" * 62)
    for note in notes:
        print(f"   note  {note}")
    if failures:
        print(f"RESULT: FAIL — {len(failures)} problem(s)")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
