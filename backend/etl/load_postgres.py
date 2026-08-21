"""Loads backend/data/*.parquet into PostgreSQL, driven entirely by manifest.yaml.

Replaces the old DuckDB builder. Same transform chain, expressed in Postgres:

    parquet --pyarrow--> staging_* (all TEXT) --SQL--> clean/cast/filter --> app.*

Why staging tables of TEXT: the source data deliberately carries numerics as
strings and uses the 4-character literal "null" instead of real NULLs. Staging
everything as TEXT means one uniform cleaning path handles both, and a value
that fails to cast becomes NULL and shows up in the null-rate report rather
than aborting the load.

ATOMIC: builds into schema `app_build`, then swaps it over `app` in a single
transaction. A live reader never sees a half-loaded schema, and a failed load
leaves the previous one untouched. The `public` schema (chat history) is never
touched by this script.

Usage:
    python -m etl.load_postgres              # load + report
    python -m etl.load_postgres --dry-run    # print the generated DDL/SQL only
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
import yaml
from dotenv import load_dotenv

ETL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ETL_DIR.parent

# The ETL reads .env directly rather than importing app.config: it must run
# without the app's other required settings (a JWT secret is not the loader's
# business), and it is often invoked before the app is configured at all.
load_dotenv(BACKEND_DIR / ".env")
DATA_DIR = BACKEND_DIR / "data"
MANIFEST_PATH = ETL_DIR / "manifest.yaml"

LIVE_SCHEMA = "app"
BUILD_SCHEMA = "app_build"
RO_ROLE = "qorvexa_ro"

# Every rep gets this password in the synthetic dataset. It is a demo fixture,
# not a secret — but it is still stored only as a bcrypt hash, because the point
# of the exercise is that the *verification path* is real.
SEED_PASSWORD = os.environ.get("SEED_REP_PASSWORD", "qorvexa")


def load_manifest() -> dict:
    with MANIFEST_PATH.open() as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------
# parquet -> Python strings
# --------------------------------------------------------------------------

def _stringify(value) -> str | None:
    """One uniform TEXT representation, so the SQL cast path is the only cleaner.

    Booleans must not go through str() (Python gives 'True', Postgres wants
    'true'), and dates/timestamps go out as ISO so the casts below are
    unambiguous.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def read_parquet_as_text(path: Path, source_columns: list[str]) -> list[list[str | None]]:
    table = pq.read_table(path)
    present = set(table.column_names)
    missing = [c for c in source_columns if c not in present]
    if missing:
        raise SystemExit(
            f"{path.name}: manifest names column(s) not present in the parquet: "
            f"{missing}\nParquet has: {sorted(present)}"
        )
    columns = {name: table.column(name).to_pylist() for name in source_columns}
    return [
        [_stringify(columns[name][i]) for name in source_columns]
        for i in range(table.num_rows)
    ]


# --------------------------------------------------------------------------
# manifest -> SQL
# --------------------------------------------------------------------------

def column_specs(cfg: dict) -> list[tuple[str, str, dict]]:
    """[(source_name, canonical_name, spec_dict), ...] for one table."""
    out = []
    for source, spec in cfg["columns"].items():
        if isinstance(spec, str):
            out.append((source, spec, {}))
        else:
            out.append((source, spec["rename"], spec))
    return out


def clean_expr(source: str, spec: dict) -> str:
    """The SELECT expression that turns one staged TEXT column into its final value.

    Mirrors what the DuckDB builder did with NULLIF + TRY_CAST. Postgres has no
    TRY_CAST, so `pg_input_is_valid` (PG16+) does the same job without the
    whole statement erroring on one bad row.
    """
    ref = f's."{source}"'

    sentinel = spec.get("null_sentinel")
    if sentinel is not None:
        ref = f"NULLIF(btrim({ref}), {_lit(sentinel)})"
    else:
        ref = f"NULLIF(btrim({ref}), '')"

    pg_type = spec.get("type")
    if pg_type is None:
        return ref

    if pg_type == "date":
        # Dates arrive both as '2026-07-08' and as '2026-07-08T00:00:00.000Z'.
        # timestamptz accepts both, so one guarded cast covers them.
        return (
            f"CASE WHEN pg_input_is_valid({ref}, 'timestamptz') "
            f"THEN (({ref})::timestamptz)::date END"
        )

    return (
        f"CASE WHEN pg_input_is_valid({ref}, {_lit(pg_type)}) "
        f"THEN ({ref})::{pg_type} END"
    )


def _lit(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


NAME_NORM_SQL = (
    # Must stay in step with normalize_name() in app/bot/resolve.py — the fuzzy
    # matcher compares its Python-normalised query against this stored value.
    "upper(regexp_replace("
    "  regexp_replace(btrim({col}), '^(DR\\.?|DOCTOR)\\s+', '', 'i'),"
    "  '\\s+', ' ', 'g'))"
)


def build_table_ddl(table: str, cfg: dict) -> str:
    cols = []
    for _source, canonical, spec in column_specs(cfg):
        pg_type = spec.get("type", "text")
        cols.append(f'    "{canonical}" {pg_type}')
    if cfg.get("name_norm_from"):
        cols.append('    "name_norm" text')
    body = ",\n".join(cols)
    return f"CREATE TABLE {BUILD_SCHEMA}.{table} (\n{body}\n);"


def build_insert_sql(table: str, cfg: dict) -> str:
    specs = column_specs(cfg)
    target_cols = [c for _s, c, _sp in specs]
    select_exprs = [f'{clean_expr(s, sp)} AS "{c}"' for s, c, sp in specs]

    if cfg.get("name_norm_from"):
        target_cols.append("name_norm")
        norm_source = next(
            s for s, c, _ in specs if c == cfg["name_norm_from"]
        )
        norm_spec = next(sp for s, c, sp in specs if c == cfg["name_norm_from"])
        select_exprs.append(
            NAME_NORM_SQL.format(col=clean_expr(norm_source, norm_spec)) + ' AS "name_norm"'
        )

    quoted_targets = ", ".join(f'"{c}"' for c in target_cols)
    select_list = ",\n           ".join(select_exprs)
    sql = (
        f"INSERT INTO {BUILD_SCHEMA}.{table} ({quoted_targets})\n"
        f"    SELECT {select_list}\n"
        f"    FROM {BUILD_SCHEMA}.staging_{table} s"
    )

    vintage = cfg.get("vintage_column")
    if vintage:
        # Drop stale re-sends: keep only rows at the newest load_date. The
        # doctors file deliberately carries 12 older rows to exercise this.
        vintage_source = next(s for s, c, _ in specs if c == vintage)
        vintage_spec = next(sp for s, c, sp in specs if c == vintage)
        vintage_clean = clean_expr(vintage_source, vintage_spec)
        sql += (
            f"\n    WHERE {vintage_clean} = ("
            f"SELECT MAX({vintage_clean.replace('s.', 's2.')}) "
            f"FROM {BUILD_SCHEMA}.staging_{table} s2)"
        )
    return sql + ";"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        sys.exit(
            "DATABASE_URL is not set.\n"
            "  export DATABASE_URL=postgresql://qorvexa:qorvexa@127.0.0.1:5432/qorvexa"
        )
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print SQL, touch nothing")
    args = parser.parse_args()

    manifest = load_manifest()
    tables = manifest["tables"]

    # The parquet sources are no longer committed — etl/seed_app.sql is, and it is
    # what setup.sh, docker-compose and CI apply. This loader is still the
    # authority on the manifest -> DDL mapping and on every cleaning transform, so
    # it stays; it just cannot run without the sources. Fail here with an
    # actionable message rather than at the first pyarrow open, which reports only
    # a missing filename.
    if not args.dry_run:
        missing = [
            cfg["source_file"]
            for cfg in tables.values()
            if not (DATA_DIR / cfg["source_file"]).exists()
        ]
        if missing:
            raise SystemExit(
                f"{len(missing)} parquet source(s) are not present under {DATA_DIR}.\n"
                "\n"
                "The committed dataset is etl/seed_app.sql — load that instead:\n"
                f"    psql -v ON_ERROR_STOP=1 \"$DATABASE_URL\" -f etl/seed_app.sql\n"
                "\n"
                "This loader is only needed to REGENERATE that file after a\n"
                "manifest change, and then it needs the parquet sources. See DATA.md.\n"
                "`--dry-run` still works: it prints the DDL and needs no data."
            )
    views = manifest.get("views") or {}
    indexes = manifest.get("indexes") or {}

    if args.dry_run:
        for table, cfg in tables.items():
            print(build_table_ddl(table, cfg))
            print(build_insert_sql(table, cfg))
            print()
        return

    report: list[str] = []
    vintage_rows: list[tuple[str, str, int]] = []

    with psycopg.connect(dsn(), autocommit=False) as conn:
        conn.execute("SET TIME ZONE 'UTC'")  # deterministic date casts

        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {BUILD_SCHEMA} CASCADE")
            cur.execute(f"CREATE SCHEMA {BUILD_SCHEMA}")
        conn.commit()

        # The view bodies in the manifest name tables unqualified (they have to:
        # they are the same SQL that will run against the live `app` schema).
        # Point the search_path at the build schema so they resolve here.
        # Postgres stores a view's dependencies by OID, so the schema rename at
        # the end carries them over intact.
        conn.execute(f"SET search_path TO {BUILD_SCHEMA}, public")
        conn.commit()

        for table, cfg in tables.items():
            specs = column_specs(cfg)
            source_columns = [s for s, _c, _sp in specs]
            path = DATA_DIR / cfg["source_file"]
            if not path.exists():
                sys.exit(f"{path} not found — nothing to load.")

            rows = read_parquet_as_text(path, source_columns)

            with conn.cursor() as cur:
                staging_cols = ",\n".join(f'    "{c}" text' for c in source_columns)
                cur.execute(
                    f"CREATE TABLE {BUILD_SCHEMA}.staging_{table} (\n{staging_cols}\n)"
                )
                quoted = ", ".join(f'"{c}"' for c in source_columns)
                with cur.copy(
                    f"COPY {BUILD_SCHEMA}.staging_{table} ({quoted}) FROM STDIN"
                ) as copy:
                    for row in rows:
                        copy.write_row(row)

                cur.execute(build_table_ddl(table, cfg))
                cur.execute(build_insert_sql(table, cfg))

                cur.execute(f"SELECT count(*) FROM {BUILD_SCHEMA}.{table}")
                rows_out = cur.fetchone()[0]

                report.append(f"\n=== {table} ===")
                report.append(f"  parquet rows: {len(rows):,}")
                report.append(f"  loaded rows:  {rows_out:,}")
                if len(rows) != rows_out:
                    report.append(
                        f"  -> {len(rows) - rows_out} row(s) dropped by the vintage filter"
                    )

                # Null-rate report for every cleaned column. A column that comes
                # out 100% NULL means the cast or the sentinel is misconfigured —
                # this is the check that catches a silently-wrong manifest.
                for _s, canonical, spec in specs:
                    if not (spec.get("type") or spec.get("null_sentinel")):
                        continue
                    if rows_out == 0:
                        continue
                    cur.execute(
                        f'SELECT round(100.0 * count(*) FILTER '
                        f'(WHERE "{canonical}" IS NULL) / count(*), 1) '
                        f"FROM {BUILD_SCHEMA}.{table}"
                    )
                    pct = cur.fetchone()[0]
                    flag = "   <-- ALL NULL, check type/null_sentinel" if pct == 100 else ""
                    report.append(f"  {canonical}: {pct}% NULL{flag}")
                    if spec.get("pii"):
                        report[-1] += "  [PII]"

                if cfg.get("vintage_column") and rows_out:
                    cur.execute(
                        f'SELECT max("{cfg["vintage_column"]}")::text '
                        f"FROM {BUILD_SCHEMA}.{table}"
                    )
                    vintage_rows.append((table, cur.fetchone()[0], rows_out))

                cur.execute(f"DROP TABLE {BUILD_SCHEMA}.staging_{table}")
            conn.commit()

        # Views after the tables, so a stale view fails here (loudly, at load
        # time) rather than at query time.
        with conn.cursor() as cur:
            for view, vcfg in views.items():
                body = vcfg["sql"].strip().rstrip(";")
                try:
                    cur.execute(f"CREATE VIEW {BUILD_SCHEMA}.{view} AS {body}")
                except psycopg.Error as exc:
                    sys.exit(
                        f"view '{view}' failed to build — its SQL is out of step "
                        f"with the tables above it:\n  {exc}"
                    )
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                    (BUILD_SCHEMA, view),
                )
                actual = [r[0] for r in cur.fetchall()]
                declared = vcfg["columns"]
                if actual != declared:
                    sys.exit(
                        f"view '{view}': the manifest declares columns\n"
                        f"  {declared}\n"
                        f"but the created view actually exposes\n"
                        f"  {actual}\n"
                        "The `columns:` list and the `sql:` are out of step. "
                        "app/bot/schema.py trusts the declared list, so this "
                        "must not diverge."
                    )
                report.append(f"\n=== {view} (view) ===")
                report.append(f"  columns verified against manifest: {', '.join(actual)}")

            for table, cols in indexes.items():
                for col in cols:
                    cur.execute(
                        f"CREATE INDEX idx_{table}_{col} "
                        f'ON {BUILD_SCHEMA}.{table} ("{col}")'
                    )

            # Build metadata the app surfaces as "Data as of ...".
            cur.execute(
                f"CREATE TABLE {BUILD_SCHEMA}.data_vintage "
                "(table_name text, max_load_date text, row_count bigint)"
            )
            cur.executemany(
                f"INSERT INTO {BUILD_SCHEMA}.data_vintage VALUES (%s, %s, %s)",
                vintage_rows,
            )

            # Auth column lives with the reps it belongs to. Hashes are computed
            # here rather than committed, so no hash ever enters the repo.
            import bcrypt

            cur.execute(f"ALTER TABLE {BUILD_SCHEMA}.reps ADD COLUMN password_hash text")
            digest = bcrypt.hashpw(SEED_PASSWORD.encode(), bcrypt.gensalt()).decode()
            cur.execute(f"UPDATE {BUILD_SCHEMA}.reps SET password_hash = %s", (digest,))
        conn.commit()

        # Atomic swap.
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {LIVE_SCHEMA} CASCADE")
            cur.execute(f"ALTER SCHEMA {BUILD_SCHEMA} RENAME TO {LIVE_SCHEMA}")
        conn.commit()

        # Read-only role: the tool layer's DSN. This is a stronger boundary than
        # the old process-level read_only flag, because the server enforces it.
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_ROLE,))
            if cur.fetchone() is None:
                cur.execute(
                    f"CREATE ROLE {RO_ROLE} LOGIN PASSWORD "
                    f"{_lit(os.environ.get('RO_PASSWORD', 'qorvexa_ro'))}"
                )
            cur.execute(f"GRANT USAGE ON SCHEMA {LIVE_SCHEMA} TO {RO_ROLE}")
            cur.execute(
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {LIVE_SCHEMA} TO {RO_ROLE}"
            )
            cur.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {LIVE_SCHEMA} "
                f"GRANT SELECT ON TABLES TO {RO_ROLE}"
            )
            # Explicitly deny the write paths, so a future GRANT cannot widen
            # this role by accident.
            cur.execute(f"REVOKE CREATE ON SCHEMA {LIVE_SCHEMA} FROM {RO_ROLE}")
            # The auth column is STRUCTURALLY unreadable by the role model-
            # composed SQL runs as — the same reasoning that keeps chat history,
            # checkpoints and Google tokens in un-granted schemas. Before this,
            # only run_sql's denylist and column enumeration stood between the
            # model and every rep's bcrypt hash (audit finding M-SEC1). Column-level:
            # REVOKE SELECT(col) alone would leave the rest of the table
            # readable but Postgres has no "all but one column" grant, so the
            # whole-table SELECT is revoked and re-granted on the named columns.
            cur.execute(f"REVOKE SELECT ON {LIVE_SCHEMA}.reps FROM {RO_ROLE}")
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = 'reps' "
                "AND column_name <> 'password_hash'",
                (LIVE_SCHEMA,),
            )
            safe_columns = ", ".join(row[0] for row in cur.fetchall())
            cur.execute(
                f"GRANT SELECT ({safe_columns}) ON {LIVE_SCHEMA}.reps TO {RO_ROLE}"
            )
        conn.commit()

    print("LOAD REPORT")
    print("=" * 62)
    print("\n".join(report))
    print("\n=== data_vintage ===")
    for t, v, n in vintage_rows:
        print(f"  {t}: max_load_date={v}  rows={n:,}")
    print(f"\nLoaded into schema '{LIVE_SCHEMA}'. Read-only role: {RO_ROLE}")
    print(f"Seed password for all {len(vintage_rows) and 'reps'}: {SEED_PASSWORD!r}")


if __name__ == "__main__":
    main()
