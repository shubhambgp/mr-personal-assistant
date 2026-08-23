"""Qdrant: the only store for the document corpus — vectors, text and metadata.

THE ONE THING THAT MATTERS HERE. Postgres is not involved in retrieval, so
there is no SQL backstop: tenancy is enforced in exactly one place, by the
payload filter this module builds. That makes `search()` the single most
security-critical function in the RAG path, and it is written accordingly:

  * It takes a `RepContext`, not a filter. There is no parameter through which a
    caller — or a tool argument the model composed — can supply, widen or
    replace the scope predicate.
  * `_scope_filter()` is the only place the predicate exists, so there is no
    second copy to drift.
  * `scope` and `chair_id` carry payload indexes, so the filter is applied by
    the engine during search rather than as a post-hoc trim of the results.

Same discipline as the scoped CTEs in app/tools/sql_tools.py: one chokepoint,
tested directly, never reconstructed by callers.

RETRIEVAL IS HYBRID, for a correctness reason rather than a performance one.
Dense embeddings rank "Cardevia 20 mg" and "Cardevia 40 mg" as near-identical —
in a dosing answer that is a patient-safety error, not a relevance miss. So a
sparse BM25 leg runs alongside the dense one and Qdrant fuses them with RRF:
sparse pins the exact strength, dense catches the paraphrase ("can I use it with
metformin" -> "concomitant administration with biguanides").

NO SERVER REQUIRED. qdrant-client runs the real engine in-process: `path=` for
on-disk persistence in dev, `:memory:` for tests, and QDRANT_URL for a real
server. Identical API, so promoting to a server is configuration.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from qdrant_client import QdrantClient, models

from ..bot.context import RepContext
from ..config import settings

COLLECTION = "qorvexa_literature"
DENSE = "dense"
SPARSE = "bm25"

#: text-embedding-3-small
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

_client: QdrantClient | None = None


# ---------------------------------------------------------------- lifecycle ---

def open_vectors() -> QdrantClient:
    """Opens the client and ensures the collection exists. Idempotent."""
    global _client
    if _client is not None:
        return _client

    if settings.qdrant_url:
        _client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    else:
        # Local mode: the real engine, in-process. `:memory:` is used by tests.
        _client = QdrantClient(path=settings.qdrant_path)

    if not _client.collection_exists(COLLECTION):
        _client.create_collection(
            COLLECTION,
            vectors_config={
                DENSE: models.VectorParams(size=EMBEDDING_DIM, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={
                # IDF is computed by Qdrant, so the client only ever sends term
                # frequencies. That is what lets the lexical leg exist without
                # fastembed and its 66 MB of unused onnxruntime.
                SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        # Payload indexes accelerate the tenancy filter on a real server.
        #
        # In local (in-process) mode Qdrant has no payload index and warns if you
        # ask for one — so they are only created against a server. This changes
        # *speed*, not correctness: the filter is still applied during the search
        # either way, it is simply a scan rather than an index lookup. At this
        # corpus size (hundreds of chunks) that is not measurable; the indexes
        # are declared so the property survives promotion to a server.
        for field, schema in () if not settings.qdrant_url else (
            ("scope", models.PayloadSchemaType.KEYWORD),
            ("chair_id", models.PayloadSchemaType.INTEGER),
            ("doc_type", models.PayloadSchemaType.KEYWORD),
            ("brand_lc", models.PayloadSchemaType.KEYWORD),
            ("molecule_lc", models.PayloadSchemaType.KEYWORD),
            ("content_sha256", models.PayloadSchemaType.KEYWORD),
            ("pipeline_version", models.PayloadSchemaType.KEYWORD),
            ("document_id", models.PayloadSchemaType.KEYWORD),
        ):
            _client.create_payload_index(COLLECTION, field_name=field, field_schema=schema)
    return _client


def close_vectors() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None


def vectors() -> QdrantClient:
    if _client is None:
        raise RuntimeError("vector store not opened — call open_vectors() first")
    return _client


# -------------------------------------------------------------- lexical leg ---

# Deliberately small and deliberately shared. The same function tokenises at
# ingest and at query time, so the two cannot disagree — which is the failure
# mode a hand-rolled lexical leg actually has.
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")

# Words that carry no retrieval signal in this corpus. Kept short on purpose: an
# aggressive stopword list is how "no data on X" turns into "data on X".
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "to",
    "was", "were", "will", "with", "what", "which", "when", "how", "do",
    "does", "did", "can", "could", "should", "would", "my", "me",
})


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, keeping decimals so "20" and "20.5" differ.

    Drug strengths are the reason this is not a naive `.split()`: "Cardevia
    20 mg" must produce a token "20" that a query for the 20 mg dose can match
    exactly, because that distinction is clinical rather than cosmetic.
    """
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def sparse_vector(text: str) -> models.SparseVector:
    """Term-frequency weights, with Qdrant supplying IDF at search time.

    Weights are sub-linear in frequency (1 + ln tf), which is the standard
    damping: a section that says "renal" nine times is not nine times more about
    renal impairment than one that says it once.
    """
    counts = Counter(_hash_token(t) for t in tokenize(text))
    if not counts:
        # An empty sparse vector is rejected by Qdrant, and a chunk of pure
        # punctuation is not worth indexing lexically anyway.
        return models.SparseVector(indices=[], values=[])
    indices, values = zip(*sorted(counts.items()), strict=True)
    return models.SparseVector(
        indices=list(indices),
        values=[1.0 + math.log(c) for c in values],
    )


def _hash_token(token: str) -> int:
    """Stable 32-bit token id.

    Python's builtin hash() is salted per process, so it would produce different
    ids for the same word on every run — the vectors written at ingest would not
    match the vector built at query time. This is deterministic across processes.
    """
    import zlib

    return zlib.crc32(token.encode()) & 0x7FFFFFFF


# ------------------------------------------------------------------ tenancy ---

def _scope_filter(ctx: RepContext) -> models.Filter:
    """The only filter this module ever builds, and it does exactly one job.

        scope = 'global' OR chair_id = <this rep>

    NOTHING ELSE IS FILTERED, and that is a deliberate correction rather than a
    simplification. brand, molecule and doc_type were `must` conditions here, and
    each turned a helpful hint from the model into a silent exclusion:

      * brand="Zephyrion" excluded a rep's own uploaded brief, because the brand
        was not in the known list so its payload brand was null;
      * doc_type="monograph" excluded the same document again, because a file
        named "…-territory-brief.pdf" is inferred as `brief`. The model was right
        to ask for clinical facts; the heuristic classification disagreed.

    In both cases retrieval worked perfectly when asked directly, and the model
    got "no results" — the worst kind of failure, because it looks like an empty
    corpus rather than a filter mistake. The principle now is: a model-supplied
    narrowing may steer ranking, never empty the result set. Those hints are
    folded into the query text by app/tools/rag_tools.py instead.

    Leaving tenancy as the filter's only job also makes the security story
    single-purpose: there is one predicate, it comes from a frozen RepContext
    built from a verified JWT (CLAUDE.md §1.1), and no caller can pass a filter
    into search() at all.
    """
    return models.Filter(
        should=[
            models.FieldCondition(key="scope", match=models.MatchValue(value="global")),
            models.FieldCondition(
                key="chair_id", match=models.MatchValue(value=int(ctx.chair_id))
            ),
        ]
    )


# ------------------------------------------------------------------- search ---

def search(
    ctx: RepContext,
    *,
    query: str,
    dense_query: list[float] | None,
    limit: int = 5,
    candidates: int = 40,
) -> list[dict]:
    """Hybrid retrieval, scoped to this rep. Returns payloads plus scores.

    Takes a RepContext and never a filter — see the module docstring. `limit` is
    clamped because it reaches here from a model-composed tool argument.
    """
    client = vectors()
    limit = max(1, min(int(limit), 20))
    scope = _scope_filter(ctx)

    prefetch = [
        models.Prefetch(
            query=sparse_vector(query), using=SPARSE, filter=scope, limit=candidates
        )
    ]
    if dense_query is not None:
        prefetch.append(
            models.Prefetch(query=dense_query, using=DENSE, filter=scope, limit=candidates)
        )

    result = client.query_points(
        COLLECTION,
        prefetch=prefetch,
        # Reciprocal Rank Fusion, computed by Qdrant. Rank-based rather than
        # score-based, so a cosine similarity and a BM25 score never have to be
        # made commensurable — which they are not.
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=scope,
        limit=limit,
        with_payload=True,
    )
    return [{"score": p.score, **(p.payload or {})} for p in result.points]


def list_documents(ctx: RepContext, limit: int = 100) -> list[dict]:
    """Distinct documents this rep may see. Scoped through the same filter."""
    client = vectors()
    seen: dict[str, dict] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION,
            scroll_filter=_scope_filter(ctx),
            limit=256,
            offset=offset,
            with_payload=True,
        )
        for point in points:
            payload = point.payload or {}
            key = str(payload.get("document_id"))
            if key not in seen:
                seen[key] = {
                    # Projection only: what the Library page renders. Tenancy is
                    # decided entirely by _scope_filter above — never here.
                    "document_id": payload.get("document_id"),
                    "title": payload.get("title"),
                    "source_filename": payload.get("source_filename"),
                    "doc_type": payload.get("doc_type"),
                    "brand": payload.get("brand"),
                    "molecule": payload.get("molecule"),
                    "version": payload.get("version"),
                    "effective_date": payload.get("effective_date"),
                    "scope": payload.get("scope"),
                    "pages": payload.get("page_count"),
                    # Null for chunks ingested before this key existed.
                    "ingested_at": payload.get("ingested_at"),
                }
        if offset is None or len(seen) >= limit:
            break
    return sorted(seen.values(), key=lambda d: (d["doc_type"] or "", d["title"] or ""))


def document_chunks(
    ctx: RepContext, document_id: str, *, max_chars: int = 24_000
) -> tuple[list[dict], bool]:
    """Every chunk of ONE document, in order. Returns (chunks, truncated).

    A FETCH of a named object, not a search — which is why a `document_id`
    condition on top of the tenancy filter does not contradict the rule in
    `_scope_filter`. That rule exists because a model-supplied *narrowing of a
    search* must never silently empty the result set; here an empty result is
    the correct and only honest answer to "read document X" when X is not this
    rep's. Tenancy is still `_scope_filter` and nothing else: the `must` below
    combines it with the id, so a foreign document_id matches zero points.
    """
    client = vectors()
    scoped = _scope_filter(ctx)
    combined = models.Filter(
        must=[
            models.Filter(should=scoped.should),
            models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id)),
        ]
    )

    collected: list[dict] = []
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION, scroll_filter=combined, limit=256, offset=offset, with_payload=True
        )
        for point in points:
            payload = point.payload or {}
            collected.append(
                {
                    "ordinal": payload.get("ordinal") or 0,
                    "section": payload.get("section"),
                    "page_from": payload.get("page_from"),
                    "text": payload.get("text") or "",
                }
            )
        if offset is None:
            break

    collected.sort(key=lambda c: c["ordinal"])

    # Bounded on purpose: a 120-page document would otherwise put its entire
    # text into the turn's context, which is both expensive and the fastest way
    # to push the actual question out of the model's attention.
    kept: list[dict] = []
    budget = max_chars
    for chunk in collected:
        text = str(chunk["text"])
        if budget - len(text) < 0:
            return kept, True
        budget -= len(text)
        kept.append(chunk)
    return kept, False


def already_ingested(content_sha256: str, pipeline_version: str) -> bool:
    """Byte-identical re-ingest is a no-op rather than a duplicate.

    Matches on the pipeline version as well as the file hash, and that second
    condition is not incidental. Keying on the file alone means a change to the
    chunker or the embedding model leaves every existing document silently
    stale: the file has not changed, so ingestion skips it, and the index keeps
    serving chunks built by the old code. Including the pipeline version makes a
    pipeline change re-ingest itself.

    Deliberately NOT scope-filtered: this is an ingest-time question about the
    store, not a retrieval question about a rep.
    """
    got, _ = vectors().scroll(
        COLLECTION,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="content_sha256", match=models.MatchValue(value=content_sha256)
                ),
                models.FieldCondition(
                    key="pipeline_version", match=models.MatchValue(value=pipeline_version)
                ),
            ]
        ),
        limit=1,
        with_payload=False,
    )
    return bool(got)


def delete_document(document_id: str) -> None:
    vectors().delete(
        COLLECTION,
        points_selector=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document_id)
                )
            ]
        ),
    )


def upsert_chunks(points: list[models.PointStruct]) -> None:
    vectors().upsert(COLLECTION, points=points)


def count() -> int:
    return vectors().count(COLLECTION, exact=True).count
