import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("OPENAI_API_KEY", "test-key")


@pytest.fixture(scope="session")
def db_pools():
    """Opens the pools, or skips the whole module if Postgres is not reachable.

    Skipping loudly beats failing obscurely: these tests need a *loaded*
    database, and CI brings one up as a service container.
    """
    from app.bot import db

    try:
        db.open_pools()
        with db.ro_pool().connection() as conn:
            conn.execute("SELECT 1 FROM reps LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available or not loaded: {type(exc).__name__}: {exc}")
    yield db
    db.close_pools()


@pytest.fixture
def first_chair(db_pools):
    with db_pools.ro_pool().connection() as conn:
        row = conn.execute("SELECT chair_id, rep_code, rep_name FROM reps ORDER BY chair_id LIMIT 1").fetchone()
    assert row, "reps table is empty — apply etl/seed_app.sql first"
    return row
