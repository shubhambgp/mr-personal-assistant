import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Settings validate at import, so a secret must exist before `app.config` loads.
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("OPENAI_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _restore_environment():
    """Every test starts from the environment the session started with.

    Not hygiene for its own sake: `etl/ingest_docs.py` calls `load_dotenv()` at
    IMPORT time, and the upload endpoint imports it inside the handler — so the
    moment any test drives that endpoint, the developer's whole backend/.env is
    in os.environ for the rest of the session. That leak made
    test_token_crypto's "a half-configured agenda must be refused" pass alone
    and fail in the suite, because the third value it needs to be ABSENT was
    being supplied from the .env of a machine that has Google configured.

    Restoring here fixes the class rather than that one test. monkeypatch's own
    teardown runs first, so setenv/delenv still behave normally.
    """
    before = dict(os.environ)
    yield
    if os.environ != before:
        os.environ.clear()
        os.environ.update(before)
