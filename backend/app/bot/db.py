"""PostgreSQL access. Two pools, deliberately.

    ro_pool()  -> connects as the read-only role. Everything the agent's tool
                  layer touches goes through this.
    rw_pool()  -> connects as the owner. Auth lookups and chat-history writes.

Why two: the tool layer runs SQL that a language model composed. The guards in
app/tools/sql_tools.py reject writes by pattern, but a pattern is a filter, not
a boundary. The read-only role means the *server* refuses a write even if every
guard above it were bypassed. Defence in depth, and the outer layer is the one
we do not have to be clever about.

This replaces a DuckDB file opened with read_only=True and
enable_external_access=False. Those were process-level flags on an embedded
engine; a Postgres role is enforced by the server, and a non-superuser role
cannot reach pg_read_server_files or COPY ... FROM PROGRAM — so the
arbitrary-file-read class of bug those flags were holding shut is now gone by
construction rather than by configuration.
"""

from __future__ import annotations

import time

from psycopg_pool import ConnectionPool

from ..config import settings

_ro: ConnectionPool | None = None
_rw: ConnectionPool | None = None


def _make(dsn: str, *, read_only: bool) -> ConnectionPool:
    def configure(conn) -> None:
        conn.autocommit = True
        if read_only:
            # Belt and braces: the role already cannot write. This also makes a
            # write attempt fail fast with a clear error instead of a permission
            # error deep inside a statement.
            conn.execute("SET default_transaction_read_only = on")
        conn.execute(f"SET statement_timeout = {settings.statement_timeout_ms}")
        conn.execute("SET TIME ZONE 'UTC'")

    return ConnectionPool(
        dsn,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        configure=configure,
        open=False,
        name="ro" if read_only else "rw",
    )


def open_pools() -> None:
    """Called once from the FastAPI lifespan hook."""
    global _ro, _rw
    if _ro is None:
        _ro = _make(settings.database_url_ro, read_only=True)
        _ro.open(wait=True, timeout=10)
    if _rw is None:
        _rw = _make(settings.database_url, read_only=False)
        _rw.open(wait=True, timeout=10)


def close_pools() -> None:
    global _ro, _rw
    for pool in (_ro, _rw):
        if pool is not None:
            pool.close()
    _ro = _rw = None


def ro_pool() -> ConnectionPool:
    if _ro is None:
        raise RuntimeError("read-only pool not open — call open_pools() first")
    return _ro


def rw_pool() -> ConnectionPool:
    if _rw is None:
        raise RuntimeError("read-write pool not open — call open_pools() first")
    return _rw


#: data_vintage changes only when the ETL runs, yet it was queried on every
#: chat request. A short TTL keeps the "Data as of ..." line honest within five
#: minutes of a reload without a per-turn round trip. In-process, like the
#: other small caches here.
_VINTAGE_TTL_SECONDS = 300
_vintage_cache: tuple[float, list[tuple[str, str, int]]] | None = None


def data_vintage() -> list[tuple[str, str, int]]:
    """[(table_name, max_load_date, row_count), ...] — build metadata.

    Surfaced to the rep as "Data as of ...". Two different load_date vintages
    across tables is a real property of this dataset, so it is shown rather
    than reconciled away.
    """
    global _vintage_cache
    now = time.monotonic()
    if _vintage_cache is not None and now - _vintage_cache[0] < _VINTAGE_TTL_SECONDS:
        return _vintage_cache[1]
    with ro_pool().connection() as conn:
        rows = conn.execute(
            "SELECT table_name, max_load_date, row_count FROM data_vintage "
            "ORDER BY table_name"
        ).fetchall()
    result = [tuple(r) for r in rows]
    _vintage_cache = (now, result)
    return result
