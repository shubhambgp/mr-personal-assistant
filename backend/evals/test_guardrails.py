"""run_sql guardrails, exercised through the real query path. No LLM.

This file exists because of a specific miss. The natural-language eval
`pii_not_accessible` asked the model for a mobile number, the model declined,
and the eval passed — while `SELECT * FROM my_doctors` was returning the column
in its result rows the whole time. Asking in English tests the model. These
tests exercise the code.
"""

from __future__ import annotations

import json

import pytest

from app.bot import schema
from app.bot.context import RepContext
from app.tools.sql_tools import SqlToolProvider

pytestmark = pytest.mark.requires_db


@pytest.fixture
def run_sql(db_pools, first_chair):
    chair_id, rep_code, rep_name = first_chair
    ctx = RepContext(chair_id=chair_id, rep_code=rep_code, rep_name=rep_name or "Rep")
    # The POOL, as the HTTP path passes it — handlers check out per call.
    specs = {
        s["name"]: s["handler"]
        for s in SqlToolProvider().get_tools(ctx, db_pools.ro_pool())
    }
    return specs["run_sql"], chair_id


async def test_select_star_returns_rows_but_no_pii(run_sql):
    """The regression test for the leak. Rows come back; PII does not."""
    handler, _chair = run_sql
    payload = json.loads(await handler(sql="SELECT * FROM my_doctors LIMIT 5"))
    assert "error" not in payload, payload
    assert payload["rows"], "expected rows — an empty result would pass vacuously"
    returned = set().union(*(set(r) for r in payload["rows"]))
    assert not (returned & set(schema.pii_columns())), returned


async def test_pii_column_rejected_by_name(run_sql):
    handler, _chair = run_sql
    for column in schema.pii_columns():
        payload = json.loads(await handler(sql=f"SELECT {column} FROM my_doctors LIMIT 1"))
        assert "not accessible" in payload.get("error", ""), (column, payload)


@pytest.mark.parametrize(
    "relation", ["doctors", "visits", "targets", "actual_visits", "thresholds", "brands"]
)
async def test_base_relations_are_denied(run_sql, relation):
    handler, _chair = run_sql
    payload = json.loads(await handler(sql=f"SELECT * FROM {relation} LIMIT 1"))
    assert "cannot be queried directly" in payload.get("error", ""), payload


@pytest.mark.parametrize("alias", ["my_doctors", "my_brands", "my_hooks", "my_visits"])
async def test_scoped_aliases_return_only_this_chair(run_sql, alias):
    handler, chair = run_sql
    payload = json.loads(await handler(sql=f"SELECT DISTINCT chair_id FROM {alias}"))
    assert "error" not in payload, payload
    seen = {r["chair_id"] for r in payload["rows"]}
    assert seen <= {chair}, seen


async def test_group_by_works_without_selecting_chair_id(run_sql):
    """The reason scoping is done with CTEs rather than an outer WHERE."""
    handler, _chair = run_sql
    payload = json.loads(
        await handler(sql="SELECT specialty, COUNT(*) AS n FROM my_doctors GROUP BY specialty")
    )
    assert "error" not in payload, payload
    assert payload["rows"]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE doctors",
        "SELECT 1 FROM my_doctors; SELECT 1",
        "INSERT INTO my_doctors VALUES (1)",
        "UPDATE my_doctors SET specialty = 'x'",
        "COPY my_doctors TO '/tmp/leak.csv'",
        "SET ROLE postgres",
    ],
)
async def test_writes_and_stacked_statements_are_blocked(run_sql, sql):
    handler, _chair = run_sql
    payload = json.loads(await handler(sql=sql))
    assert "error" in payload, payload


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT schemaname, tablename FROM pg_catalog.pg_tables",
        "SELECT table_name FROM information_schema.tables",
        "SELECT * FROM my_doctors WHERE pg_sleep(5) IS NULL",
        "SELECT current_setting('search_path')",
    ],
)
async def test_catalog_and_introspection_are_blocked(run_sql, sql):
    """The model cannot map the hidden schemas or stall the pool (audit finding M-SEC3).

    Data in agenda/agent/public is unreachable by privilege, but table NAMES are
    visible to any role through the catalogs, and pg_sleep is a cheap DoS. None of
    these should get past run_sql's guard.
    """
    handler, _chair = run_sql
    payload = json.loads(await handler(sql=sql))
    assert "error" in payload, payload


async def test_row_limit_cannot_be_raised_by_the_model(run_sql):
    from app.tools.sql_tools import RUN_SQL_ROW_LIMIT

    handler, _chair = run_sql
    payload = json.loads(await handler(sql="SELECT doctor_id FROM my_brands LIMIT 100000"))
    assert "error" not in payload, payload
    assert len(payload["rows"]) <= RUN_SQL_ROW_LIMIT


async def test_readonly_role_cannot_read_password_hashes(db_pools):
    """The auth column is structurally unreadable by the agent's role.

    Before this REVOKE, only run_sql's regex denylist and column enumeration
    stood between model-composed SQL and every rep's bcrypt hash (audit finding
    M-SEC1). The named columns stay readable — the scoped my_reps CTE needs
    them — so both directions are asserted.
    """
    import psycopg

    from app.config import settings

    with (
        psycopg.connect(settings.database_url_ro) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("SELECT password_hash FROM reps LIMIT 1")
    # A failed statement poisons the transaction; reconnect for the positive leg.
    with psycopg.connect(settings.database_url_ro) as conn:
        row = conn.execute("SELECT chair_id, rep_code FROM reps LIMIT 1").fetchone()
        assert row is not None


async def test_readonly_role_cannot_write_even_outside_the_guards(db_pools):
    """Defence in depth: prove the *database* refuses, not just the regex.

    If this ever passes, the tool layer has been pointed at the owner DSN.
    """
    import psycopg

    with db_pools.ro_pool().connection() as conn, pytest.raises(psycopg.Error):
        conn.execute("CREATE TABLE should_not_exist (x int)")


async def test_run_sql_cannot_reach_chat_history(run_sql):
    """Chat history is not in the data manifest, so the denylist does not name it.

    The only thing keeping model-composed SQL out of every rep's transcript is
    that the read-only role has no privileges in the `public` schema. If someone
    "helpfully" grants it SELECT there, this test is what catches it.
    """
    handler, _chair = run_sql
    for relation in ("conversations", "messages", "public.conversations"):
        payload = json.loads(await handler(sql=f"SELECT * FROM {relation} LIMIT 1"))
        assert "error" in payload, (relation, payload)
        assert "permission denied" in payload["error"].lower() or "cannot be queried" in payload["error"]


# --------------------------------------------------------------------------
# LangGraph checkpoint storage. These three guard the bug found in the
# migration spike (ENGINEERING_LOG 15): AsyncPostgresSaver creates its tables
# *unqualified*, so with the app's normal search_path they would have landed in
# `app` — a schema the ETL drops on every load and which auto-grants SELECT to
# the read-only role. Both a total state loss and a cross-rep history leak, from
# one line of default configuration.
# --------------------------------------------------------------------------


async def test_checkpoint_tables_live_in_the_agent_schema(db_pools):
    """Not in `app` (dropped every ETL run) and not in `public`."""
    with db_pools.rw_pool().connection() as conn:
        rows = conn.execute(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE tablename LIKE 'checkpoint%'"
        ).fetchall()
    if not rows:
        pytest.skip("checkpointer has not been set up in this database yet")

    schemas = {schema for schema, _ in rows}
    assert schemas == {"agent"}, f"checkpoint tables outside `agent`: {rows}"


async def test_run_sql_cannot_reach_the_checkpoint_tables(run_sql):
    """Every rep's full message history lives here.

    The manifest does not name these tables, so run_sql's denylist does not
    either — the only thing keeping model-composed SQL out of them is that the
    read-only role has no privileges in the `agent` schema. Exactly the same
    shape as test_run_sql_cannot_reach_chat_history above, and exactly as easy
    to break with a "helpful" GRANT.
    """
    handler, _chair = run_sql
    for relation in (
        "checkpoints",
        "checkpoint_writes",
        "checkpoint_blobs",
        "agent.checkpoints",
        "agent.checkpoint_writes",
    ):
        payload = json.loads(await handler(sql=f"SELECT * FROM {relation} LIMIT 1"))
        assert "error" in payload, (relation, payload)
        error = payload["error"].lower()
        assert (
            "permission denied" in error
            or "does not exist" in error
            or "cannot be queried" in error
        ), (relation, payload)


async def test_a_foreign_conversation_id_never_becomes_the_graph_thread(db_pools):
    """thread_id must always be a conversation the caller owns.

    The graph keys its checkpointed state on thread_id, so if rep B could hand
    in rep A's conversation id, B would resume A's transcript. get_or_create
    filters on (id, chair_id) and falls through to creating a fresh row, so B
    gets their own new thread instead — this test is what keeps that true.
    """
    from app.bot.context import RepContext
    from app.services import conversations as convo_service

    with db_pools.ro_pool().connection() as conn:
        chairs = conn.execute(
            "SELECT chair_id, rep_code, rep_name FROM reps ORDER BY chair_id LIMIT 2"
        ).fetchall()
    if len(chairs) < 2:
        pytest.skip("need two reps to test cross-rep isolation")

    rep_a = RepContext(chair_id=chairs[0][0], rep_code=chairs[0][1], rep_name=chairs[0][2] or "A")
    rep_b = RepContext(chair_id=chairs[1][0], rep_code=chairs[1][1], rep_name=chairs[1][2] or "B")

    owned_by_a = convo_service.get_or_create(rep_a, None, "rep A's private thread")
    handed_to_b = convo_service.get_or_create(rep_b, str(owned_by_a["id"]), "B probing")

    assert str(handed_to_b["id"]) != str(owned_by_a["id"]), (
        "rep B was handed rep A's thread id — the graph would have resumed A's state"
    )

    # And the row B got really is B's.
    with db_pools.rw_pool().connection() as conn:
        owner = conn.execute(
            "SELECT chair_id FROM public.conversations WHERE id = %s", (handed_to_b["id"],)
        ).fetchone()
    assert owner is not None and owner[0] == rep_b.chair_id

    with db_pools.rw_pool().connection() as conn:
        conn.execute(
            "DELETE FROM public.conversations WHERE id IN (%s, %s)",
            (owned_by_a["id"], handed_to_b["id"]),
        )
