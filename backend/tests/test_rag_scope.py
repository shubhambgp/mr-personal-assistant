"""Tenancy for retrieval — the single most load-bearing property in the RAG path.

Qdrant is the only store, so unlike the SQL tools there is NO second authority:
if `vectors._scope_filter()` is wrong, one rep reads another rep's documents and
the results still look entirely plausible. That is why these tests exercise the
real engine (in-memory, so they need no server and no API key) rather than
mocking it — a mocked filter proves nothing about the filter.

They also try to break it on purpose: passing another rep's chair_id as a tool
argument, and asking for a brand that only exists in someone else's upload.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from qdrant_client import models

from app.bot.context import RepContext
from app.services import vectors
from app.tools.rag_tools import RagToolProvider

REP_A = RepContext(chair_id=7100001, rep_code=7800001, rep_name="Rep A")
REP_B = RepContext(chair_id=7100002, rep_code=7800002, rep_name="Rep B")

#: Distinct fake embeddings, so "nearest neighbour" is predictable.
def _vec(seed: float) -> list[float]:
    return [seed] + [0.0] * (vectors.EMBEDDING_DIM - 1)


@pytest.fixture()
def store(monkeypatch):
    """A real in-memory Qdrant seeded with one global and two rep-owned documents."""
    from qdrant_client import QdrantClient

    client = QdrantClient(":memory:")
    client.create_collection(
        vectors.COLLECTION,
        vectors_config={
            vectors.DENSE: models.VectorParams(
                size=vectors.EMBEDDING_DIM, distance=models.Distance.COSINE
            )
        },
        sparse_vectors_config={
            vectors.SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    monkeypatch.setattr(vectors, "_client", client)

    def point(pid: int, scope: str, chair_id: int | None, brand: str, text: str):
        return models.PointStruct(
            id=pid,
            vector={
                vectors.DENSE: _vec(1.0),
                vectors.SPARSE: vectors.sparse_vector(text),
            },
            payload={
                "document_id": f"doc-{pid}",
                "title": f"{brand} document {pid}",
                "scope": scope,
                "chair_id": chair_id,
                "brand": brand,
                "brand_lc": brand.lower(),
                "molecule": None,
                "molecule_lc": None,
                "doc_type": "monograph",
                "section": "4.2 Posology",
                "page_from": 1,
                "text": text,
            },
        )

    client.upsert(
        vectors.COLLECTION,
        points=[
            point(1, "global", None, "Cardevia", "Cardevia shared company monograph dosing"),
            point(2, "chair", REP_A.chair_id, "Alphadrug",
                  "Alphadrug private note belonging to rep A dosing"),
            point(3, "chair", REP_B.chair_id, "Betadrug",
                  "Betadrug private note belonging to rep B dosing"),
        ],
    )
    yield client
    vectors._client = None


def _titles(hits: list[dict]) -> set[str]:
    return {h["title"] for h in hits}


def test_a_rep_sees_global_documents_and_their_own(store):
    hits = vectors.search(REP_A, query="dosing", dense_query=_vec(1.0), limit=10)
    assert _titles(hits) == {"Cardevia document 1", "Alphadrug document 2"}


def test_a_rep_never_sees_another_reps_upload(store):
    hits = vectors.search(REP_B, query="dosing", dense_query=_vec(1.0), limit=10)
    assert _titles(hits) == {"Cardevia document 1", "Betadrug document 3"}
    assert not any("Alphadrug" in t for t in _titles(hits))


def test_naming_the_other_reps_brand_does_not_reveal_it(store, monkeypatch):
    """Asking for someone else's document by name returns theirs-not-at-all.

    Goes through the tool rather than search() directly, because the brand hint
    is a tool-level concept now: hints steer ranking and never filter, so this
    asserts the tenancy filter — not a lucky exclusion — is what withholds it.
    """

    class _Embeddings:
        async def create(self, **_kwargs):
            class _D:
                embedding = _vec(1.0)

            return type("R", (), {"data": [_D()]})()

    monkeypatch.setattr(
        "app.tools.rag_tools.get_client",
        lambda: type("C", (), {"embeddings": _Embeddings()})(),
    )
    spec = next(
        s for s in RagToolProvider().get_tools(REP_B, conn=None)
        if s["name"] == "search_literature"
    )
    raw = asyncio.run(
        spec["handler"](
            query="Alphadrug private note", brand="Alphadrug", molecule=None,
            doc_type="monograph", top_k=10,
        )
    )
    assert "Alphadrug" not in raw, raw


def test_the_tool_output_itself_carries_no_foreign_text(store, monkeypatch):
    """The check that matters: absent from the RAW tool result, not just the prose.

    A guard that only stops the model *mentioning* another rep's document still
    put that document's text into the transcript, the audit log and the UI. This
    asserts the leak never gets that far.
    """

    class _FakeEmbeddings:
        async def create(self, **_kwargs):
            class _D:
                embedding = _vec(1.0)

            return type("R", (), {"data": [_D()]})()

    monkeypatch.setattr(
        "app.tools.rag_tools.get_client",
        lambda: type("C", (), {"embeddings": _FakeEmbeddings()})(),
    )
    spec = next(
        s for s in RagToolProvider().get_tools(REP_B, conn=None)
        if s["name"] == "search_literature"
    )
    raw = asyncio.run(
        spec["handler"](query="Alphadrug private note dosing", brand=None, molecule=None,
                        doc_type=None, top_k=10)
    )
    assert "Alphadrug" not in raw, raw
    assert "rep A" not in raw, raw
    payload = json.loads(raw)
    assert payload["row_count"] >= 1  # it did return rep B's own things


def test_list_documents_is_scoped_too(store):
    """Discovery leaks as readily as search if it forgets the predicate."""
    titles_a = {d["title"] for d in vectors.list_documents(REP_A)}
    titles_b = {d["title"] for d in vectors.list_documents(REP_B)}
    assert titles_a == {"Cardevia document 1", "Alphadrug document 2"}
    assert titles_b == {"Cardevia document 1", "Betadrug document 3"}


def test_list_documents_projects_the_library_metadata(store):
    """The Library page needs document_id / filename / date — and the projection
    change must not have widened the scope: rep A still never sees rep B's doc.
    """
    docs = vectors.list_documents(REP_A)
    for doc in docs:
        # Keys are always present; values may be None for chunks ingested before
        # the field existed (ingested_at) or seeded without one (source_filename).
        assert "document_id" in doc
        assert "source_filename" in doc
        assert "ingested_at" in doc
    assert {d["document_id"] for d in docs} == {"doc-1", "doc-2"}


async def test_read_document_cannot_read_another_reps_upload(store):
    """read_document fetches by id, so it gets its own tenancy test.

    It is the one place a document_id — a value the model composes — reaches
    Qdrant. The id condition sits INSIDE a must with _scope_filter, so rep B
    naming rep A's document must come back empty rather than with the text.
    """
    from app.tools.rag_tools import RagToolProvider

    handler = next(
        s["handler"]
        for s in RagToolProvider().get_tools(REP_B, conn=None)
        if s["name"] == "read_document"
    )

    # Rep B asking for rep A's document by name: refused at the resolve step,
    # because list_documents is scoped too.
    payload = json.loads(await handler(name="Alphadrug"))
    assert "error" in payload, payload
    assert "Alphadrug" not in json.dumps(payload.get("available") or [])

    # And their own document reads fine, so the guard is not just refusing.
    mine = json.loads(await handler(name="Betadrug"))
    assert "error" not in mine, mine
    assert mine["section_count"] >= 1


async def test_read_document_chunks_are_scoped_at_the_store(store):
    """Belt and braces: even given rep A's document_id directly, the fetch
    returns nothing for rep B — the filter, not the resolve step, is the
    boundary."""
    foreign = [
        d["document_id"] for d in vectors.list_documents(REP_A) if d["document_id"] == "doc-2"
    ]
    assert foreign == ["doc-2"], "fixture changed: doc-2 should be rep A's"

    chunks, _ = vectors.document_chunks(REP_B, "doc-2")
    assert chunks == []


def test_search_takes_no_filter_argument():
    """The structural guarantee, asserted so a future 'convenience' cannot undo it.

    If `search()` ever grows a filter/where/query_filter parameter, a tool
    argument the model composed could widen the scope predicate. The absence of
    that parameter is the security property, so it is tested as one.
    """
    import inspect

    params = set(inspect.signature(vectors.search).parameters)
    assert not params & {"filter", "query_filter", "scope", "chair_id", "where"}
    assert "ctx" in params
