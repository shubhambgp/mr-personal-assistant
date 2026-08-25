"""SqlToolProvider — the field-force data tools, over PostgreSQL.

SECURITY, in one paragraph: the rep's chair_id is bound into every closure in
this module. It is never a parameter any tool accepts, so the model has no way
to ask for another rep's data. `run_sql` — the escape hatch that lets the model
write its own SQL — never touches a base table: every query is prefixed with
CTEs that expose only this rep's slice, named `my_<relation>`, and any
reference to a base relation is rejected. Do not add a chair_id/rep_id
parameter to any tool here; ToolRegistry will reject it, and the reason is in
CLAUDE.md.

Ported from a DuckDB implementation. Three things changed and are worth
knowing:

1. `?` placeholders became `%s` (psycopg paramstyle).
2. `TRY_CAST` does not exist in Postgres. The one place it was load-bearing
   (ordering brands by their P1..P6 priority) is now a guarded regexp cast.
3. The query timeout was a nested daemon thread calling DuckDB's
   `cursor.interrupt()`. It is now `SET LOCAL statement_timeout` inside a
   transaction — Postgres cancels the query itself. Less code, no thread, and
   it actually covers the case where the client stops waiting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, timedelta

from psycopg.rows import dict_row

from ..bot import resolve, schema
from ..bot.context import RepContext
from ..config import settings
from ..services import agenda as agenda_service
from .base import ToolSpec

log = logging.getLogger(__name__)

RUN_SQL_ROW_LIMIT = 200

# Write verbs plus the Postgres-specific escapes worth naming: COPY can read and
# write server files, and SET/RESET could change the session out from under us.
FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"vacuum|analyze|cluster|reindex|call|do|execute|prepare|deallocate|"
    r"listen|notify|lock|set|reset|discard|refresh|import|export)\b",
    re.IGNORECASE,
)

# System-catalog and introspection access. The read-only role cannot read the
# DATA in the hidden schemas (agenda/agent/public have no grant), but table and
# column NAMES are visible to any role through pg_catalog / information_schema —
# which hands a map of every schema to a model, and defeats §1.6's "the data
# model is not user-facing" at the metadata level. pg_sleep is a cheap DoS
# bounded only by the statement timeout; current_setting can read GUCs. None of
# these has any legitimate use in a rep's question. See audit finding M-SEC3.
# `pg_[a-z_]+` already covers pg_catalog, pg_tables, pg_sleep, pg_read_file, etc.
# Bare `version` is deliberately omitted: it would collide with a plausible column
# name, and version() leaks nothing that matters.
FORBIDDEN_SQL_INTROSPECTION = re.compile(
    r"\b(pg_[a-z_]+|information_schema|current_setting|set_config)\b",
    re.IGNORECASE,
)


def _base_relations_pattern() -> re.Pattern[str]:
    """Every physical table and view name — built from the manifest, not hardcoded.

    Word boundaries deliberately do NOT match the `my_` aliases: an underscore
    is a word character, so `my_doctors` contains no standalone `doctors`.
    """
    names = sorted(schema.base_relations(), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.IGNORECASE)


def scoped_ctes(chair_id: int) -> dict[str, str]:
    """{my_<relation>: SQL} for every relation in the manifest.

    Columns are enumerated explicitly rather than `SELECT *`. That is the fix
    for a real leak, not a style choice: the PII guard below is a regex over the
    query *text*, so `SELECT * FROM my_doctors` never types `mobile` and
    therefore passed the guard while returning the column in the result rows.
    Naming only non-PII columns closes it at the source.
    """
    chair = int(chair_id)  # ours, never model-supplied — safe to interpolate
    kinds = schema.scope_kinds()
    out: dict[str, str] = {}

    for relation, columns in schema.queryable_columns().items():
        cols = ", ".join(f'"{c}"' for c in columns)
        # .get(..., "chair") fails closed: an unrecognised relation is treated
        # as rep-owned and filtered, rather than exposed.
        kind = kinds.get(relation, "chair")
        if kind == "global":
            out[f"my_{relation}"] = f"SELECT {cols} FROM {relation}"
        elif kind == "doctor":
            out[f"my_{relation}"] = (
                f"SELECT {cols} FROM {relation} WHERE doctor_id IN "
                f"(SELECT doctor_id FROM doctors WHERE chair_id = {chair})"
            )
        else:
            out[f"my_{relation}"] = f"SELECT {cols} FROM {relation} WHERE chair_id = {chair}"
    return out


def scoped_table_list() -> str:
    return ", ".join(sorted(f"my_{r}" for r in schema.queryable_columns()))


def scoped_schema_text() -> str:
    """The relation/column reference, for the run_sql tool description only.

    This used to sit in the system prompt, where the model read it as general
    knowledge and would happily recite it ("list all the tables"). Here it is
    operating detail for one tool. Same information available for composing SQL,
    without inviting the model to treat the data model as a topic.
    """
    return "\n".join(
        f"  my_{relation}({', '.join(columns)})"
        for relation, columns in sorted(schema.queryable_columns().items())
    )


def build_scoped_query(user_sql: str, chair_id: int) -> str:
    ctes = ",\n".join(f"{alias} AS ({body})" for alias, body in scoped_ctes(chair_id).items())
    stripped = user_sql.lstrip()
    if re.match(r"^with\b", stripped, re.IGNORECASE):
        # Their query already opens with WITH — splice ours in ahead of theirs.
        rest = re.sub(r"^with\b", "", stripped, count=1, flags=re.IGNORECASE).lstrip()
        return f"WITH {ctes},\n{rest}"
    return f"WITH {ctes}\n{stripped}"


# ---------------------------------------------------------------------------
# Shared SQL
# ---------------------------------------------------------------------------

# brand_priority is text 'P1'..'P6' (and sometimes the literal "null"). Order by
# its number, nulls last. DuckDB's TRY_CAST has no Postgres equivalent, so strip
# non-digits and cast what remains; NULLIF keeps an empty result NULL rather
# than erroring.
_BRAND_PRIORITY_ORDER = "NULLIF(regexp_replace(brand_priority, '\\D', '', 'g'), '')::int"

_BRAND_SQL = f"""
    SELECT brand_name, final_rcpa, cm_prescribed_qty, pm_prescribed_qty,
           cm_target, pm_target, brand_priority, brand_rank, growth_booster
    FROM brands
    WHERE chair_id = %s AND doctor_id = %s
    ORDER BY {_BRAND_PRIORITY_ORDER} NULLS LAST, brand_rank
"""

_HOOKS_SQL = """
    SELECT notes, hook_category, product_desc, work_date
    FROM hooks
    WHERE chair_id = %s AND doctor_id = %s {category_filter}
    ORDER BY CASE WHEN hook_category = 'Samples' THEN 1 ELSE 0 END ASC,
             work_date DESC
    LIMIT 8
"""


class SqlToolProvider:
    """The nine data tools plus the composite daily plan."""

    name = "sql"

    def get_tools(self, ctx: RepContext, db) -> list[ToolSpec]:
        chair_id = ctx.chair_id
        denied_pattern = _base_relations_pattern()

        # --- query helpers -------------------------------------------------
        # psycopg is synchronous; to_thread keeps one slow query from stalling
        # every other request's event-loop turn.
        #
        # `db` is the read-only POOL. A connection is checked out per call and
        # released before the handler returns — the previous shape pinned one
        # connection for the whole SSE turn, so ten concurrent chats exhausted
        # a ten-connection pool while the database sat ~97.5% idle waiting on
        # the model. A checkout is microseconds; the turn is tens of seconds.

        def _fetch(sql: str, params: tuple = ()) -> list[dict]:
            with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return cur.fetchall()

        async def q(sql: str, params: tuple = ()) -> list[dict]:
            return await asyncio.to_thread(_fetch, sql, params)

        async def q1(sql: str, params: tuple = ()) -> dict | None:
            rows = await q(sql, params)
            return rows[0] if rows else None

        async def _latest_period(table: str) -> tuple[int, int] | None:
            row = await q1(
                f"SELECT year, month FROM {table} WHERE chair_id = %s "
                "ORDER BY year DESC, month DESC LIMIT 1",
                (chair_id,),
            )
            return (row["year"], row["month"]) if row else None

        # --- tools ---------------------------------------------------------

        def _find_doctor_sync(name: str) -> list[dict]:
            # resolve takes a connection, not a pool — checked out only for
            # the duration of the lookup.
            with db.connection() as conn:
                return resolve.find_doctor_candidates(conn, chair_id, name)

        async def find_doctor(name: str) -> str:
            candidates = await asyncio.to_thread(_find_doctor_sync, name)
            return json.dumps({"candidates": candidates}, default=str)

        async def get_doctor_brief(doctor_id: int) -> str:
            doctor = await q1(
                """
                SELECT d.doctor_name, d.specialty, d.clinic_name, d.city_name,
                       d.avg_3_month_rcpa, d.scientific_engagement, d.patient_support,
                       c.persona_prescriber, c.persona_type, c.persona_loyalty,
                       c.persona_digital, c.avg_rcpa_national
                FROM doctors d
                LEFT JOIN doctor_codes c ON c.doctor_id = d.doctor_id
                WHERE d.chair_id = %s AND d.doctor_id = %s
                """,
                (chair_id, doctor_id),
            )
            if doctor is None:
                return json.dumps({"error": "This doctor is not in your book."})

            last = await q1(
                "SELECT MAX(work_date) AS last_visit_date FROM actual_visits "
                "WHERE chair_id = %s AND doctor_id = %s",
                (chair_id, doctor_id),
            )
            doctor["last_visit_date"] = last["last_visit_date"] if last else None

            doctor["visit_status"] = await q1(
                """
                SELECT year, month, number_of_visits, visit_freq,
                       required_visits, visit_pending_this_month
                FROM required_pending_visits
                WHERE chair_id = %s AND doctor_id = %s
                ORDER BY year DESC, month DESC LIMIT 1
                """,
                (chair_id, doctor_id),
            )
            doctor["top_brands"] = (await q(_BRAND_SQL, (chair_id, doctor_id)))[:5]
            doctor["hooks"] = (
                await q(_HOOKS_SQL.format(category_filter=""), (chair_id, doctor_id))
            )[:5]
            return json.dumps(doctor, default=str)

        async def get_doctor_hooks(doctor_id: int, category: str | None = None) -> str:
            if category:
                sql = _HOOKS_SQL.format(category_filter="AND hook_category = %s")
                params: tuple = (chair_id, doctor_id, category)
            else:
                sql = _HOOKS_SQL.format(category_filter="")
                params = (chair_id, doctor_id)
            return json.dumps({"hooks": await q(sql, params)}, default=str)

        async def get_doctor_brands(doctor_id: int) -> str:
            return json.dumps(
                {"brands": await q(_BRAND_SQL, (chair_id, doctor_id))}, default=str
            )

        async def list_pending_visits(
            year: int | None = None, month: int | None = None, limit: int | None = None
        ) -> str:
            limit = min(limit or 20, 100)
            if year is None or month is None:
                period = await _latest_period("required_pending_visits")
                if period is None:
                    return json.dumps({"pending": []})
                year, month = period

            rows = await q(
                """
                SELECT r.doctor_id, d.doctor_name, r.number_of_visits, r.required_visits,
                       (r.required_visits - r.number_of_visits) AS shortfall
                FROM required_pending_visits r
                JOIN doctors d ON d.doctor_id = r.doctor_id AND d.chair_id = r.chair_id
                WHERE r.chair_id = %s AND r.year = %s AND r.month = %s
                  AND r.visit_pending_this_month > 0
                ORDER BY shortfall DESC
                LIMIT %s
                """,
                (chair_id, year, month, limit),
            )
            return json.dumps(
                {"year": year, "month": month, "pending": rows}, default=str
            )

        async def get_visit_summary(year: int | None = None, month: int | None = None) -> str:
            if year is None or month is None:
                period = await _latest_period("required_pending_visits")
                if period is None:
                    return json.dumps({"error": "No visit-requirement data for this chair."})
                year, month = period

            # EXTRACT returns numeric in Postgres, so cast before comparing to an int.
            required = await q1(
                "SELECT SUM(required_visits) AS required, SUM(number_of_visits) AS recorded "
                "FROM required_pending_visits WHERE chair_id = %s AND year = %s AND month = %s",
                (chair_id, year, month),
            )
            planned = await q1(
                "SELECT COUNT(*) AS n FROM planned_visits WHERE chair_id = %s "
                "AND EXTRACT(YEAR FROM visit_date)::int = %s "
                "AND EXTRACT(MONTH FROM visit_date)::int = %s",
                (chair_id, year, month),
            )
            actual = await q1(
                "SELECT COUNT(*) AS n FROM actual_visits WHERE chair_id = %s "
                "AND EXTRACT(YEAR FROM work_date)::int = %s "
                "AND EXTRACT(MONTH FROM work_date)::int = %s",
                (chair_id, year, month),
            )
            accompanied = await q(
                "SELECT accompanied_by AS who, COUNT(*) AS count FROM actual_visits "
                "WHERE chair_id = %s AND EXTRACT(YEAR FROM work_date)::int = %s "
                "AND EXTRACT(MONTH FROM work_date)::int = %s "
                "GROUP BY accompanied_by ORDER BY count DESC",
                (chair_id, year, month),
            )
            return json.dumps(
                {
                    "year": year,
                    "month": month,
                    "required_visits": required["required"] if required else None,
                    "actual_visits_recorded_against_requirement": (
                        required["recorded"] if required else None
                    ),
                    "planned_visits": planned["n"] if planned else 0,
                    "actual_visits": actual["n"] if actual else 0,
                    "accompanied_by_breakdown": accompanied,
                },
                default=str,
            )

        async def get_rep_scorecard(year: int | None = None, month: int | None = None) -> str:
            if year is None or month is None:
                period = await _latest_period("rep_metrics")
                if period is None:
                    return json.dumps({"error": "No scorecard data for this chair."})
                year, month = period

            return json.dumps(
                {
                    "year": year,
                    "month": month,
                    "metrics": await q1(
                        "SELECT no_of_doctors, mcr_count, mcr_coverage, mv_frequency, "
                        "average_calls_per_day FROM rep_metrics "
                        "WHERE chair_id = %s AND year = %s AND month = %s",
                        (chair_id, year, month),
                    ),
                    "thresholds": await q1(
                        "SELECT mcr_threshold, mvc_threshold FROM thresholds "
                        "ORDER BY load_date DESC LIMIT 1"
                    ),
                    "cluster_band": await q1(
                        """
                        SELECT lt.cluster_name, lt.performance_lower_limit,
                               lt.performance_upper_limit
                        FROM reps r
                        JOIN leaderboard_thresholds lt ON lt.sbu_id = r.sbu_id
                        WHERE r.chair_id = %s
                        """,
                        (chair_id,),
                    ),
                },
                default=str,
            )

        async def get_doctor_chemists(doctor_id: int) -> str:
            rows = await q(
                "SELECT chemist_id, chemist_name, last_work_date FROM chemists "
                "WHERE chair_id = %s AND doctor_id = %s "
                "ORDER BY last_work_date DESC NULLS LAST LIMIT 20",
                (chair_id, doctor_id),
            )
            return json.dumps({"chemists": rows}, default=str)

        async def get_daily_plan(limit: int | None = None) -> str:
            """Composite: merges ranked signals from several contributors.

            Written as a composer rather than one query on purpose. Today every
            contributor is SQL; the next milestones add an email contributor
            (via MCP) and a document contributor (via RAG). Extending this means
            appending to `contributors` — no restructuring.
            """
            limit = min(limit or 8, 25)

            async def _pending_signal() -> list[dict]:
                period = await _latest_period("required_pending_visits")
                if period is None:
                    return []
                year, month = period
                rows = await q(
                    """
                    SELECT r.doctor_id, d.doctor_name, d.specialty, d.city_name,
                           (r.required_visits - r.number_of_visits) AS shortfall
                    FROM required_pending_visits r
                    JOIN doctors d ON d.doctor_id = r.doctor_id AND d.chair_id = r.chair_id
                    WHERE r.chair_id = %s AND r.year = %s AND r.month = %s
                      AND r.visit_pending_this_month > 0
                    ORDER BY shortfall DESC
                    LIMIT %s
                    """,
                    (chair_id, year, month, limit),
                )
                return [
                    {
                        "source": "visit_shortfall",
                        "doctor_id": r["doctor_id"],
                        "doctor_name": r["doctor_name"],
                        "specialty": r["specialty"],
                        "city": r["city_name"],
                        "weight": float(r["shortfall"] or 0),
                        "why": f"{r['shortfall']} visit(s) short this month",
                    }
                    for r in rows
                ]

            async def _hook_signal() -> list[dict]:
                rows = await q(
                    """
                    SELECT h.doctor_id, d.doctor_name, h.hook_category, h.notes, h.work_date
                    FROM hooks h
                    JOIN doctors d ON d.doctor_id = h.doctor_id AND d.chair_id = h.chair_id
                    WHERE h.chair_id = %s AND h.hook_category IS NOT NULL
                      AND h.hook_category <> 'Samples'
                    ORDER BY h.work_date DESC NULLS LAST
                    LIMIT %s
                    """,
                    (chair_id, limit),
                )
                return [
                    {
                        "source": "engagement_hook",
                        "doctor_id": r["doctor_id"],
                        "doctor_name": r["doctor_name"],
                        "weight": 1.0,
                        "why": f"{r['hook_category']}: {(r['notes'] or '')[:160]}",
                    }
                    for r in rows
                ]

            async def _mail_signal() -> list[dict]:
                """The email contributor this docstring has always named.

                Returns [] when no mailbox is connected, so an unconnected rep
                gets exactly the plan they get today.
                """
                items = await agenda_service.needs_action(ctx, limit=limit)
                return [
                    {
                        "source": "mail",
                        "doctor_id": i.doctor_id,
                        "doctor_name": i.doctor_name,
                        "weight": i.weight,
                        # days_waiting is IN the tool output, not implied by two
                        # dates: check_grounding rejects any number in the answer
                        # that did not come from a tool result this turn.
                        "why": f"{i.category}: {i.subject[:80]} "
                        f"({i.days_waiting} day(s) waiting)",
                    }
                    for i in items
                    if i.doctor_id is not None
                ]

            async def _task_signal() -> list[dict]:
                """The rep's own to-do list, and the assistant's notes to them."""
                try:
                    rows = await asyncio.to_thread(
                        agenda_service.list_tasks,
                        ctx,
                        status="open",
                        due_before=date.today() + timedelta(days=7),
                        limit=limit * 2,
                    )
                except Exception:  # noqa: BLE001 — a plan must not fail on tasks
                    return []
                return [
                    {
                        "source": "task",
                        "doctor_id": r["doctor_id"],
                        "doctor_name": None,
                        "weight": 2.5 if r["due_date"] else 1.5,
                        "why": f"task: {str(r['title'])[:80]}"
                        + (f" (due {r['due_date']})" if r["due_date"] else ""),
                    }
                    for r in rows
                    if r["doctor_id"] is not None
                ]

            contributors = [_pending_signal, _hook_signal, _mail_signal, _task_signal]
            # Contributors are independent, so gather rather than await in turn.
            results = await asyncio.gather(*(c() for c in contributors))

            merged: dict[int, dict] = {}
            for signal in [s for group in results for s in group]:
                entry = merged.setdefault(
                    signal["doctor_id"],
                    {
                        "doctor_id": signal["doctor_id"],
                        "doctor_name": signal.get("doctor_name"),
                        "specialty": signal.get("specialty"),
                        "city": signal.get("city"),
                        "score": 0.0,
                        "reasons": [],
                    },
                )
                entry["score"] += signal["weight"]
                entry["reasons"].append({"source": signal["source"], "detail": signal["why"]})
                # A doctor surfaced by two different signals is a stronger call
                # than one surfaced twice by the same signal.
                entry["score"] += 0.5 * (len({r["source"] for r in entry["reasons"]}) - 1)

            plan = sorted(merged.values(), key=lambda e: e["score"], reverse=True)[:limit]

            # Everything that has no doctor to merge onto. The merge is keyed on
            # doctor_id, so a team meeting or a task about nobody in particular
            # would otherwise be silently dropped from the very list that is
            # supposed to be the rep's whole morning.
            also_today: list[dict] = []
            try:
                for item in await agenda_service.needs_action(ctx, limit=limit):
                    if item.doctor_id is None:
                        also_today.append(
                            {
                                "source": "mail",
                                "what": f"{item.from_name}: {item.subject[:80]}",
                                "why": item.reason,
                            }
                        )
                for row in await asyncio.to_thread(
                    agenda_service.list_tasks, ctx, status="open", limit=limit
                ):
                    if row["doctor_id"] is None:
                        also_today.append(
                            {
                                "source": "task",
                                "what": str(row["title"])[:80],
                                "why": f"due {row['due_date']}" if row["due_date"] else "no due date",
                            }
                        )
                for event in await agenda_service.events(
                    ctx, from_date=date.today(), to_date=date.today()
                ):
                    also_today.append(
                        {
                            "source": "calendar",
                            "what": event["title"],
                            "why": f"starts {event['start']}",
                        }
                    )
            except Exception:  # noqa: BLE001 — the plan is the point; extras are extra
                # Logged rather than silently dropped: every other broad except
                # in this repo says what it swallowed, and "calendar quietly
                # missing from the daily plan" is exactly the kind of fault a
                # rep reports as 'the assistant forgot my meeting'.
                log.warning("daily plan: calendar/tasks extras unavailable", exc_info=True)

            return json.dumps(
                {
                    "plan": plan,
                    "also_today": also_today,
                    "contributors_used": [
                        "visit_shortfall",
                        "engagement_hook",
                        "mail",
                        "task",
                        "calendar",
                    ],
                    "contributors_pending": [],
                },
                default=str,
            )

        async def run_sql(sql: str) -> str:
            cleaned = sql.strip().rstrip(";").strip()

            if not re.match(r"^\s*(select|with)\b", cleaned, re.IGNORECASE):
                return json.dumps(
                    {"error": "Only SELECT (or WITH ... SELECT) statements are allowed."}
                )
            if ";" in cleaned:
                return json.dumps({"error": "Only a single statement is allowed."})
            if FORBIDDEN_SQL_KEYWORDS.search(cleaned):
                return json.dumps({"error": "Query contains a disallowed keyword."})
            if FORBIDDEN_SQL_INTROSPECTION.search(cleaned):
                return json.dumps(
                    {
                        "error": (
                            "System catalogs and introspection functions are not "
                            "accessible. Ask about the rep's own data instead."
                        )
                    }
                )
            for pii_col in schema.pii_columns():
                if re.search(rf"\b{re.escape(pii_col)}\b", cleaned, re.IGNORECASE):
                    return json.dumps(
                        {"error": f"Column '{pii_col}' is not accessible via run_sql."}
                    )

            denied = denied_pattern.search(cleaned)
            if denied:
                relation = denied.group(0)
                return json.dumps(
                    {
                        "error": (
                            f"Relation '{relation}' cannot be queried directly. Use the "
                            f"rep-scoped 'my_{relation.lower()}' instead — it contains only "
                            f"this rep's rows, so you do not need (and should not add) a "
                            f"chair_id filter or column."
                        )
                    }
                )

            scoped = build_scoped_query(cleaned, chair_id)
            # Outer LIMIT wraps the whole query, so the model cannot raise the cap
            # with its own LIMIT.
            limited = f"SELECT * FROM ({scoped}) AS _scoped_result LIMIT {RUN_SQL_ROW_LIMIT}"

            def _run() -> list[dict]:
                # SET LOCAL needs a transaction; Postgres then cancels the query
                # itself when it overruns, and the setting reverts on commit.
                with (
                    db.connection() as conn,
                    conn.transaction(),
                    conn.cursor(row_factory=dict_row) as cur,
                ):
                    cur.execute(
                        f"SET LOCAL statement_timeout = {settings.run_sql_timeout_ms}"
                    )
                    cur.execute(limited)
                    return cur.fetchall()

            try:
                rows = await asyncio.to_thread(_run)
            except Exception as exc:  # noqa: BLE001 — reported to the model, never raised
                message = str(exc).strip().splitlines()[0]
                if "statement timeout" in message.lower() or "canceling" in message.lower():
                    message = (
                        f"Query exceeded the "
                        f"{settings.run_sql_timeout_ms / 1000:g}s timeout and was cancelled."
                    )
                return json.dumps({"error": f"Query failed: {message}"})

            return json.dumps({"rows": rows, "row_count": len(rows)}, default=str)

        # --- specs ---------------------------------------------------------
        doctor_id_param = {
            "type": "object",
            "properties": {
                "doctor_id": {"type": "integer", "description": "The doctor's id, from find_doctor."}
            },
            "required": ["doctor_id"],
            "additionalProperties": False,
        }
        period_params = {
            "type": "object",
            "properties": {
                "year": {"type": ["integer", "null"], "description": "Defaults to the most recent available."},
                "month": {"type": ["integer", "null"], "description": "1-12; defaults to the most recent available."},
            },
            "required": ["year", "month"],
            "additionalProperties": False,
        }

        return [
            {
                "name": "find_doctor",
                "description": (
                    "Resolve a doctor's name to their doctor_id within the rep's own book. "
                    "Always call this first before any other doctor-specific tool — never "
                    "guess a doctor_id from a name. If more than one candidate comes back, "
                    "ask the rep which doctor they mean; do not pick the top match silently."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The doctor's name as the rep typed or said it.",
                        }
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
                "handler": find_doctor,
            },
            {
                "name": "get_doctor_brief",
                "description": (
                    "Pre-call briefing for one doctor: persona, RCPA, visit status, top brands "
                    "and engagement talking points. Primary tool for \"what should I discuss "
                    "with Dr X\" — prefer it over calling get_doctor_hooks/get_doctor_brands "
                    "separately."
                ),
                "parameters": doctor_id_param,
                "handler": get_doctor_brief,
            },
            {
                "name": "get_doctor_hooks",
                "description": (
                    "Engagement talking points for a doctor (the raw notes), ranked with rarer, "
                    "higher-value hooks (RCPA/GSP/Topic) before routine Samples entries."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doctor_id": {"type": "integer", "description": "The doctor's id, from find_doctor."},
                        "category": {
                            "type": ["string", "null"],
                            "enum": ["RCPA", "GSP", "Topic", "Samples", None],
                            "description": "Optional filter; null for all categories.",
                        },
                    },
                    "required": ["doctor_id", "category"],
                    "additionalProperties": False,
                },
                "handler": get_doctor_hooks,
            },
            {
                "name": "get_doctor_brands",
                "description": (
                    "Brand-wise prescription data for a doctor: RCPA, prescribed quantity "
                    "(current vs previous month), target, priority (P1 highest) and rank."
                ),
                "parameters": doctor_id_param,
                "handler": get_doctor_brands,
            },
            {
                "name": "list_pending_visits",
                "description": (
                    "Doctors with a visit still pending this month, ordered by the largest "
                    "shortfall against their required visit count."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "year": {"type": ["integer", "null"], "description": "Defaults to the most recent available."},
                        "month": {"type": ["integer", "null"], "description": "1-12; defaults to the most recent available."},
                        "limit": {"type": ["integer", "null"], "description": "Max rows; defaults to 20, capped at 100."},
                    },
                    "required": ["year", "month", "limit"],
                    "additionalProperties": False,
                },
                "handler": list_pending_visits,
            },
            {
                "name": "get_visit_summary",
                "description": (
                    "Planned vs actual vs required visit counts for the rep's own book in one "
                    "month, plus a breakdown of which manager(s) accompanied actual visits."
                ),
                "parameters": period_params,
                "handler": get_visit_summary,
            },
            {
                "name": "get_rep_scorecard",
                "description": (
                    "The rep's own performance metrics (MCR coverage, visit frequency, average "
                    "calls per day) against the current thresholds and their cluster band."
                ),
                "parameters": period_params,
                "handler": get_rep_scorecard,
            },
            {
                "name": "get_doctor_chemists",
                "description": "Chemists (pharmacies) tagged to a doctor, with last recorded work date.",
                "parameters": doctor_id_param,
                "handler": get_doctor_chemists,
            },
            {
                "name": "get_daily_plan",
                "description": (
                    "A ranked suggested call list for today, merging visit shortfall with "
                    "recent engagement hooks. Use for open-ended questions like \"what should "
                    "I do today\" or \"who should I visit\". Each entry carries the reasons it "
                    "was chosen, so you can explain the ranking to the rep."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": ["integer", "null"], "description": "Max doctors; defaults to 8, capped at 25."}
                    },
                    "required": ["limit"],
                    "additionalProperties": False,
                },
                "handler": get_daily_plan,
            },
            {
                "name": "run_sql",
                "description": (
                    "Escape hatch for questions the other tools don't cover. Read-only, a "
                    "single SELECT (or WITH ... SELECT) statement.\n"
                    "\n"
                    "Query ONLY these rep-scoped relations, which already contain just this "
                    "rep's rows:\n"
                    f"{scoped_schema_text()}\n"
                    "\nThese names are internal. Never repeat them, or their columns, to "
                    "the rep — see the instructions on what to say instead.\n"
                    "\n"
                    "They have the same columns as the underlying tables. Because the scoping "
                    "is already applied, do NOT add a chair_id filter and do NOT select "
                    "chair_id just to satisfy one — plain queries and GROUP BY/aggregates both "
                    "work normally. Referencing a base relation (e.g. `doctors` instead of "
                    f"`my_doctors`) is rejected. Results are capped at {RUN_SQL_ROW_LIMIT} "
                    "rows. Personal contact columns are blocked. Prefer the other tools when "
                    "they cover the question; use this only for genuinely novel lookups.\n"
                    "\n"
                    "Example: SELECT specialty, COUNT(*) AS n FROM my_doctors "
                    "GROUP BY specialty ORDER BY n DESC"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "A single read-only SELECT over the my_* scoped relations.",
                        }
                    },
                    "required": ["sql"],
                    "additionalProperties": False,
                },
                "handler": run_sql,
            },
        ]
