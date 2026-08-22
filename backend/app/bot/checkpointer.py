"""The LangGraph checkpointer: durable graph state, and the reason HITL is possible.

Two things here are load-bearing rather than incidental.

1. THE SEARCH PATH. AsyncPostgresSaver has no schema parameter — .setup() runs
   `CREATE TABLE IF NOT EXISTS checkpoints ...` unqualified. With the app's normal
   DSN (`search_path=app,public`) those tables would land in `app`, which the ETL
   drops on every load and which auto-grants SELECT to the read-only role. So this
   module builds its own DSN pinned to `search_path=agent`. See
   etl/agent_schema.sql and ENGINEERING_LOG entry 15 for the full reasoning.

2. THE POOL IS SEPARATE FROM db.py's. The checkpointer needs async connections
   with autocommit and a dict row factory; db.py's pools are synchronous and
   carry a different search_path. Sharing one would mean compromising both.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from ..config import settings

#: The schema created by etl/agent_schema.sql. Not configurable on purpose: the
#: SQL file, this constant and the revoke statements have to agree, and a knob
#: is one more thing that can disagree.
AGENT_SCHEMA = "agent"

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


def agent_dsn() -> str:
    """The owner DSN with search_path forced to the agent schema.

    Rewrites rather than appends: the source DSN already carries
    `options=-csearch_path%3Dapp,public`, and a second options parameter would be
    ignored or would win unpredictably depending on the driver.
    """
    parsed = urlparse(settings.database_url)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k != "options"]
    query.append(("options", f"-csearch_path={AGENT_SCHEMA}"))
    return urlunparse(parsed._replace(query=urlencode(query)))


async def _configure(conn) -> None:
    # AsyncPostgresSaver requires autocommit and dict rows. prepare_threshold=0
    # because the saver issues many one-shot statements and PgBouncer-style
    # poolers choke on server-side prepared statements.
    conn.prepare_threshold = 0
    conn.row_factory = dict_row


async def open_checkpointer() -> AsyncPostgresSaver:
    """Opens the pool and ensures the checkpoint tables exist. Idempotent."""
    global _pool, _saver
    if _saver is not None:
        return _saver

    _pool = AsyncConnectionPool(
        conninfo=agent_dsn(),
        min_size=1,
        max_size=settings.pool_max_size,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        configure=_configure,
        open=False,
        name="checkpointer",
    )
    await _pool.open(wait=True, timeout=10)

    _saver = AsyncPostgresSaver(_pool)  # type: ignore[arg-type]
    # Creates checkpoints / checkpoint_blobs / checkpoint_writes /
    # checkpoint_migrations inside `agent`, because of the search_path above.
    await _saver.setup()
    return _saver


async def close_checkpointer() -> None:
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
    _pool = _saver = None


def checkpointer() -> AsyncPostgresSaver:
    if _saver is None:
        raise RuntimeError("checkpointer not opened — call open_checkpointer() first")
    return _saver
