"""POST /api/documents through the real transport.

The endpoint had zero tests, and its history is exactly the class of bug that
only shows at the transport (ENGINEERING_LOG 3: upload limits that were silently
inert; audit M-BE6: a "rejected" file that stayed retrievable). Covered here:
the 415/400/413 branches, the page-cap path actually deleting what it ingested,
the per-rep upload throttle, and — mechanism-level, in the style of
test_logging_extras.py — that the body read is BOUNDED, because an unbounded
read()-then-check was a worker-memory spike any authenticated rep could drive.

DB-light: identity is dependency-overridden and the ingest pipeline is faked.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import documents as documents_module
from app.bot.context import RepContext
from app.deps import current_rep
from app.main import app

CTX = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Test Rep")


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[current_rep] = lambda: CTX
    documents_module._upload_limiter._hits.clear()
    # The handler builds the embedding client eagerly; no key is set in tests.
    monkeypatch.setattr(documents_module, "get_client", lambda: None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _ingest_result(**overrides):
    base = {"status": "ok", "pages": 3, "chunks": 5, "detail": None, "document_id": "doc-1"}
    return SimpleNamespace(**{**base, **overrides})


def _fake_ingest(result, calls=None):
    async def ingest_file(path, *, client, scope, chair_id, overrides):
        if calls is not None:
            calls.append({"scope": scope, "chair_id": chair_id, "name": path.name})
        return result

    return ingest_file


def _post(client, name="notes.pdf", data=b"%PDF-1.4 test"):
    return client.post("/api/documents", files={"file": (name, data, "application/pdf")})


def test_a_wrong_suffix_is_415(client):
    assert _post(client, name="notes.txt").status_code == 415


def test_an_empty_file_is_400(client):
    assert _post(client, data=b"").status_code == 400


def test_an_oversized_body_is_413_not_an_oom(client, monkeypatch):
    monkeypatch.setattr("etl.ingest_docs.ingest_file", _fake_ingest(_ingest_result()))
    big = b"x" * (documents_module.MAX_UPLOAD_BYTES + 1)
    response = _post(client, data=big)
    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


def test_the_read_is_bounded_at_the_source():
    """The mechanism, not one example: the handler must never call a bare
    read(). The whole body used to be pulled into RAM before the size check, so
    the 413 arrived after the memory spike, not instead of it."""
    source = inspect.getsource(documents_module)
    assert "file.read(MAX_UPLOAD_BYTES + 1)" in source
    assert "await file.read()" not in source


def test_tenancy_comes_from_the_token_not_the_request(client, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr("etl.ingest_docs.ingest_file", _fake_ingest(_ingest_result(), calls))

    response = _post(client)
    assert response.status_code == 201
    assert calls == [{"scope": "chair", "chair_id": CTX.chair_id, "name": "notes.pdf"}]


def test_the_page_cap_actually_deletes_what_it_ingested(client, monkeypatch):
    """Audit M-BE6: the rep used to be told the file was rejected while its full
    content stayed retrievable in their corpus."""
    monkeypatch.setattr(
        "etl.ingest_docs.ingest_file",
        _fake_ingest(_ingest_result(pages=documents_module.MAX_PAGES + 1)),
    )
    deleted: list[str] = []
    monkeypatch.setattr(documents_module.vectors, "delete_document", deleted.append)

    response = _post(client)
    assert response.status_code == 413
    assert deleted == ["doc-1"], "the oversized document was left retrievable"


def test_uploads_are_throttled_per_rep(client):
    # Bad-suffix posts are cheap and still consume the bucket — the throttle
    # runs first, because it is a spend control, not a validation nicety.
    for _ in range(documents_module._upload_limiter.max_attempts):
        assert _post(client, name="notes.txt").status_code == 415
    throttled = _post(client, name="notes.txt")
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) > 0
