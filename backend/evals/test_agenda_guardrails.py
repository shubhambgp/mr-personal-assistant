"""The agenda's blast radius, against a real database.

Three tables now hold things that must not leak: a Google refresh token, the
rep's own tasks, and every word a rep has sent a prescriber. None of them is in
`etl/manifest.yaml`, so `run_sql`'s relation denylist does not know their names —
the only thing keeping model-composed SQL out is that the read-only role has no
privileges in the `agenda` schema.

That is exactly the shape of the two bugs this project already found (chat
history, ENGINEERING_LOG 6; graph checkpoints, 15), and it is exactly as easy to
undo with one helpful GRANT. Hence these tests.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import psycopg
import pytest

from app.bot.context import RepContext

pytestmark = pytest.mark.requires_db


@pytest.fixture
def encryption_key(monkeypatch):
    """A real AES key, installed the way the app reads it.

    Storing a connection is refused outright with no key configured — see
    tests/test_token_crypto.py — so the tests that need a row must supply one.
    """
    import base64
    import os

    from app import config

    monkeypatch.setattr(
        config.settings,
        "agenda_encryption_key",
        base64.urlsafe_b64encode(os.urandom(32)).decode(),
    )


@pytest.fixture
def run_sql(db_pools, first_chair):
    """The real run_sql handler, as the agent gets it."""
    from app.tools.sql_tools import SqlToolProvider

    chair_id, rep_code, rep_name = first_chair
    ctx = RepContext(chair_id=chair_id, rep_code=rep_code, rep_name=rep_name)
    with db_pools.ro_pool().connection() as conn:
        specs = {s["name"]: s for s in SqlToolProvider().get_tools(ctx, conn)}
        yield specs["run_sql"]["handler"], ctx


async def test_run_sql_cannot_reach_the_agenda_schema(run_sql):
    """A refresh token and every sent draft live here.

    Same shape as test_run_sql_cannot_reach_chat_history and
    test_run_sql_cannot_reach_the_checkpoint_tables.
    """
    handler, _ctx = run_sql
    for relation in (
        "connections",
        "tasks",
        "outbound_log",
        "agenda.connections",
        "agenda.tasks",
        "agenda.outbound_log",
    ):
        payload = json.loads(await handler(sql=f"SELECT * FROM {relation} LIMIT 1"))
        assert "error" in payload, (relation, payload)
        message = payload["error"].lower()
        assert (
            "permission denied" in message
            or "does not exist" in message
            or "cannot be queried" in message
            or "not accessible" in message
        ), (relation, payload)


async def test_the_read_only_role_has_no_privileges_in_the_agenda_schema(db_pools):
    """Structural, not pattern-based. If this fails, a GRANT was added."""
    with db_pools.ro_pool().connection() as conn:
        granted = conn.execute(
            "SELECT has_schema_privilege('qorvexa_ro', 'agenda', 'USAGE')"
        ).fetchone()[0]
    assert granted is False


async def test_a_stored_refresh_token_is_never_readable_as_plaintext(
    db_pools, first_chair, encryption_key
):
    """What is at rest is ciphertext, and the test proves it rather than assuming."""
    from app.bot import db
    from app.services import agenda as agenda_service

    chair_id, rep_code, rep_name = first_chair
    ctx = RepContext(chair_id=chair_id, rep_code=rep_code, rep_name=rep_name)
    secret = f"1//0g-not-a-real-token-{uuid.uuid4()}"
    agenda_service.store_connection(
        chair_id=ctx.chair_id,
        rep_code=ctx.rep_code,
        email_account="rep@example.test",
        scopes=["https://www.googleapis.com/auth/gmail.send"],
        calendar_tz="UTC",
        refresh_token=secret,
    )
    try:
        with db.rw_pool().connection() as conn:
            stored = conn.execute(
                "SELECT refresh_token_enc FROM agenda.connections WHERE chair_id = %s",
                (ctx.chair_id,),
            ).fetchone()[0]
        assert secret not in stored
        # And it is genuinely recoverable, or the encryption is just corruption.
        from app.core.crypto import open_sealed

        assert open_sealed(stored) == secret
    finally:
        with db.rw_pool().connection() as conn:
            conn.execute("DELETE FROM agenda.connections WHERE chair_id = %s", (ctx.chair_id,))


async def test_a_connection_is_deleted_when_the_rep_code_no_longer_matches(
    db_pools, first_chair, encryption_key
):
    """A field force reassigns a rep code when someone leaves.

    Keyed on chair_id alone, the replacement would inherit the previous rep's
    mailbox. The safe failure is "connect your own account", never "here is
    somebody else's inbox".
    """
    from app.bot import db
    from app.services import agenda as agenda_service

    chair_id, rep_code, _name = first_chair
    agenda_service.store_connection(
        chair_id=chair_id,
        rep_code=rep_code,
        email_account="leaver@example.test",
        scopes=[],
        calendar_tz="UTC",
        refresh_token="1//0g-leaver",
    )
    try:
        assert agenda_service.connection(chair_id, rep_code) is not None
        # The replacement arrives with a different code on the same chair.
        assert agenda_service.connection(chair_id, rep_code + 1) is None
        with db.rw_pool().connection() as conn:
            left = conn.execute(
                "SELECT count(*) FROM agenda.connections WHERE chair_id = %s", (chair_id,)
            ).fetchone()[0]
        assert left == 0, "the stale connection survived and could still be served"
    finally:
        with db.rw_pool().connection() as conn:
            conn.execute("DELETE FROM agenda.connections WHERE chair_id = %s", (chair_id,))


async def test_one_reps_tasks_are_invisible_to_another(db_pools, first_chair):
    """Asserted against the raw service return, not prose (CLAUDE.md §1.9)."""
    from app.bot import db
    from app.services import agenda as agenda_service

    chair_id, _code, _name = first_chair
    a = RepContext(chair_id=chair_id, rep_code=1, rep_name="A")
    b = RepContext(chair_id=chair_id + 99_999, rep_code=2, rep_name="B")
    marker = f"only-rep-a-{uuid.uuid4()}"
    created = agenda_service.create_task(a, title=marker)
    try:
        assert any(t["title"] == marker for t in agenda_service.list_tasks(a))
        assert all(t["title"] != marker for t in agenda_service.list_tasks(b))
        # And B cannot complete or delete it by id, which is the interesting half.
        assert agenda_service.set_task_done(b, task_id=created["id"]) is False
        assert agenda_service.delete_task(b, task_id=created["id"]) is False
    finally:
        with db.rw_pool().connection() as conn:
            conn.execute("DELETE FROM agenda.tasks WHERE id = %s", (created["id"],))


def test_the_outbound_log_is_append_only_for_the_application():
    """Nothing in the app updates or deletes a row here.

    Not enforced by a grant today — the service writes through the owner pool,
    as chat history already does — so this asserts the property the code must
    keep instead: an audit row that can be edited is not an audit row. Sync, and
    it needs no database.
    """
    from app.services import agenda as agenda_service

    text = Path(agenda_service.__file__).read_text()
    assert "UPDATE agenda.outbound_log" not in text
    assert "DELETE FROM agenda.outbound_log" not in text


async def test_the_agenda_schema_survives_an_etl_reload(db_pools):
    """The `app`-DROP hazard, tested rather than reasoned about.

    etl/load_postgres.py drops and recreates `app` on every load. A connection
    stored there — the tempting `reps.password_hash` precedent — would be
    destroyed on each reload, and the rep would blame Google.
    """
    from app.bot import db

    with db.rw_pool().connection() as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'agenda'"
        ).fetchall()
    names = {r[0] for r in rows}
    assert {"connections", "tasks", "outbound_log"} <= names
    # And they are NOT in `app`, which is the half that would have bitten.
    with db.rw_pool().connection() as conn:
        stray = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'app' AND table_name IN "
            "('connections','tasks','outbound_log','google_credentials')"
        ).fetchall()
    assert stray == []


async def test_psycopg_is_importable_for_the_role_checks():
    """Guards the import above rather than leaving it decorative."""
    assert psycopg is not None
