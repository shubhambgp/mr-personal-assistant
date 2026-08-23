"""Ingests PDF and DOCX documents into Qdrant: parse, chunk, embed, upsert.

    python -m etl.ingest_docs data/literature --scope global
    python -m etl.ingest_docs ~/mydocs --scope chair --chair-id 7100001

Importable on purpose: POST /api/documents reuses `ingest_file` rather than
reimplementing it, so a rep's upload and the company library go through exactly
the same parser, chunker and embedder — and the same tenancy rules.

Idempotent by content hash. Re-running over an unchanged folder reports
"skipped, unchanged" and spends nothing on embeddings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
# Read .env directly rather than importing app.config: ingestion has no reason
# to require a JWT secret, and demanding one would make this unrunnable in
# contexts where only the OpenAI key and Qdrant matter.
load_dotenv(BACKEND / ".env")

from qdrant_client import models  # noqa: E402

from app.services.vectors import (  # noqa: E402
    DENSE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    SPARSE,
    already_ingested,
    close_vectors,
    count,
    delete_document,
    open_vectors,
    sparse_vector,
    upsert_chunks,
)
from etl.docs_chunk import chunk_pages  # noqa: E402
from etl.docs_parse import SUPPORTED, extraction_report, parse  # noqa: E402
from etl.literature.brands import BRANDS  # noqa: E402


def index_text(meta: dict, section: str | None, content: str) -> str:
    """The text that gets EMBEDDED and lexically indexed — not what is displayed.

    This is a contextual chunk header, and it fixes a real retrieval failure
    rather than being decoration. Cardevia's renal-dosing section reads:

        "eGFR 30-59 mL/min: maximum 20 mg once daily. eGFR below 30 mL/min: ..."

    It never says "Cardevia". So the query "maximum Cardevia dose in renal
    impairment" could not reach it on either leg — lexically there is no brand
    token to match, and semantically the chunk is about eGFR, not about a brand.
    Measured before this existed, the chunk that literally answers that question
    was not in the top 3; the front-matter chunk won instead, purely because it
    repeats the brand name.

    Prepending the brand, molecule, title and section makes each chunk
    self-describing. `payload["text"]` keeps the clean content, so the citation
    and what the model reads are unaffected.
    """
    parts = [p for p in (meta.get("brand"), meta.get("molecule")) if p]
    header = " ".join(parts)
    if header:
        header += " — "
    header += str(meta.get("title") or "")
    if section:
        header += f" — section {section}"
    return f"{header}\n{content}"


#: Bumped whenever parsing, chunking or the contextual header changes shape.
#: `already_ingested` matches on this as well as the file hash, so a pipeline
#: change re-ingests instead of leaving the index quietly built by old code.
PIPELINE_VERSION = "2026-08-23.2-section-aware-contextual"

#: OpenAI's embeddings endpoint accepts many inputs per call; batching keeps the
#: request count (and therefore the wall clock) down on a first full ingest.
EMBED_BATCH = 64

#: Rough public pricing for text-embedding-3-small, only ever used to print an
#: estimate. Wrong by a constant factor is fine; the point is that a full ingest
#: shows what it cost rather than being silent about it.
USD_PER_MILLION_TOKENS = 0.02


@dataclass
class FileResult:
    path: Path
    status: str  # "ingested" | "skipped" | "failed"
    chunks: int = 0
    pages: int = 0
    tokens: int = 0
    thin_pages: list[int] | None = None
    detail: str = ""
    #: Set when the document exists in the store (ingested, or skipped-as-
    #: unchanged). The API needs it to delete a document it decides to reject
    #: AFTER ingest — e.g. the page-count cap (audit finding M-BE6).
    document_id: str | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def infer_metadata(path: Path, text: str) -> dict:
    """Best-effort title/type/brand/molecule.

    Filename conventions cover the generated corpus; a content scan covers an
    arbitrary uploaded file, which is the case that actually needs help. Brand
    is matched against the known brand list rather than guessed, so an unrelated
    document simply gets no brand rather than a wrong one.
    """
    stem = path.stem
    lowered = f"{stem} {text[:4000]}".lower()

    if "smpc" in stem.lower() or "summary of product characteristics" in lowered:
        doc_type = "monograph"
    elif "sop" in stem.lower() or "standard operating procedure" in lowered:
        doc_type = "sop"
    elif "detailing" in stem.lower() or "objection" in stem.lower():
        doc_type = "detailing_aid"
    else:
        doc_type = "brief"

    brand = molecule = None
    for candidate, spec in BRANDS.items():
        if candidate.lower() in lowered:
            brand, molecule = candidate, spec["molecule"]
            break
    else:
        for candidate, spec in BRANDS.items():
            if spec["molecule"].lower() in lowered:
                brand, molecule = candidate, spec["molecule"]
                break

    title = text.split("\n", 1)[0].strip() if text else stem
    if not title or len(title) > 140:
        title = stem.replace("-", " ").replace("_", " ").title()

    if brand is None:
        # An uploaded document about a product we have never heard of still has a
        # name, and "Zephyrion (Zephyrionate) 15 mg …" leads with it. Better a
        # heuristic brand than a null one: it is what list_documents shows the
        # rep, and what the chunk header uses to make the text self-describing.
        lead = title.split("(")[0].strip().split()
        if lead and lead[0][:1].isupper() and lead[0].isalpha() and len(lead[0]) > 3:
            brand = lead[0]

    return {"title": title, "doc_type": doc_type, "brand": brand, "molecule": molecule}


def _cache_key(sha: str, ordinal: int) -> str:
    return f"{sha}:{ordinal}"


def load_embedding_cache(path: Path) -> dict[str, list[float]]:
    """Committed corpus embeddings, so ingestion can run with no API key.

    This is what makes the retrieval eval a free, offline, every-pull-request
    gate rather than something that only runs where a secret exists. The corpus
    is static and the embeddings are derived from it, so caching them is no less
    reproducible than caching the corpus itself.

    Stored as float16: 470 KB instead of 986 KB, and verified not to change the
    top-10 ranking — cosine ordering is robust well below float32 precision.
    """
    import numpy as np

    with np.load(path, allow_pickle=False) as blob:
        keys = [str(k) for k in blob["keys"]]
        vectors_array = blob["vectors"].astype("float32")
    if len(keys) != len(vectors_array):
        raise RuntimeError(f"{path.name}: {len(keys)} keys but {len(vectors_array)} vectors")
    return {k: v.tolist() for k, v in zip(keys, vectors_array, strict=True)}


def write_embedding_cache(path: Path, entries: dict[str, list[float]]) -> int:
    """Writes the cache and returns its size in bytes."""
    import numpy as np

    keys = sorted(entries)
    np.savez_compressed(
        path,
        keys=np.array(keys),
        vectors=np.array([entries[k] for k in keys], dtype="float16"),
    )
    return path.stat().st_size


async def _embed(client, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        response = await client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        for item in response.data:
            vector = item.embedding
            if len(vector) != EMBEDDING_DIM:
                raise RuntimeError(
                    f"embedding dimension {len(vector)} != collection dimension "
                    f"{EMBEDDING_DIM}; the collection would silently reject these"
                )
            out.append(vector)
    return out


async def ingest_file(
    path: Path,
    *,
    client,
    scope: str,
    chair_id: int | None,
    force: bool = False,
    overrides: dict | None = None,
    embedding_cache: dict[str, list[float]] | None = None,
    collect_embeddings: dict[str, list[float]] | None = None,
) -> FileResult:
    """Parses, chunks, embeds and upserts one file. The only ingest path.

    `embedding_cache` supplies pre-computed vectors so the whole pipeline can run
    offline; `collect_embeddings` captures freshly computed ones so the cache can
    be regenerated. Exactly one of them is used in any given run.
    """
    if path.suffix.lower() not in SUPPORTED:
        return FileResult(path, "skipped", detail=f"unsupported type {path.suffix}")

    sha = await asyncio.to_thread(_sha256, path)
    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"qorvexa-doc:{sha}"))
    if already_ingested(sha, PIPELINE_VERSION):
        if not force:
            return FileResult(
                path,
                "skipped",
                detail="unchanged (same content hash and pipeline version)",
                document_id=doc_id,
            )
        # A forced re-ingest replaces rather than duplicates.
        delete_document(doc_id)

    # Parsing (pypdf over up to 20 MB / 120 pages) is sync CPU work: off the
    # event loop, or one upload stalls every concurrent SSE stream in the
    # process (audit finding M-BE6).
    pages = await asyncio.to_thread(parse, path)
    report = extraction_report(pages)
    joined = "\n".join(p.text for p in pages)
    if not joined.strip():
        return FileResult(path, "failed", pages=report["pages"], detail="no text extracted")

    meta = infer_metadata(path, joined) | (overrides or {})

    chunks = await asyncio.to_thread(chunk_pages, pages)
    if not chunks:
        return FileResult(path, "failed", pages=report["pages"], detail="no chunks produced")
    document_id = doc_id

    # Embed and lexically index the *contextualised* text, not the raw chunk.
    indexed = [index_text(meta, c.section, c.content) for c in chunks]

    if embedding_cache is not None:
        missing = [
            _cache_key(sha, c.ordinal)
            for c in chunks
            if _cache_key(sha, c.ordinal) not in embedding_cache
        ]
        if missing:
            return FileResult(
                path,
                "failed",
                pages=report["pages"],
                detail=(
                    f"{len(missing)} chunk(s) absent from the embedding cache — the corpus "
                    f"or the pipeline changed since it was written. Regenerate with "
                    f"--write-embeddings."
                ),
            )
        dense_vectors = [embedding_cache[_cache_key(sha, c.ordinal)] for c in chunks]
    else:
        dense_vectors = await _embed(client, indexed)
        if collect_embeddings is not None:
            for chunk, vector in zip(chunks, dense_vectors, strict=True):
                collect_embeddings[_cache_key(sha, chunk.ordinal)] = vector

    points: list[models.PointStruct] = []
    for chunk, dense, indexed_text in zip(chunks, dense_vectors, indexed, strict=True):
        payload = {
            "document_id": document_id,
            "title": meta["title"],
            "source_filename": path.name,
            "doc_type": meta["doc_type"],
            # Tenancy. Read only through vectors._scope_filter().
            "scope": scope,
            "chair_id": chair_id,
            "brand": meta["brand"],
            "brand_lc": (meta["brand"] or "").lower() or None,
            "molecule": meta["molecule"],
            "molecule_lc": (meta["molecule"] or "").lower() or None,
            "version": meta.get("version"),
            "effective_date": meta.get("effective_date"),
            "page_count": report["pages"],
            # Display metadata only — deliberately NOT part of PIPELINE_VERSION,
            # so adding it does not force a re-ingest of the whole corpus. Chunks
            # ingested before this key existed simply read None, and the UI
            # renders nothing for them.
            "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "content_sha256": sha,
            "pipeline_version": PIPELINE_VERSION,
            "ordinal": chunk.ordinal,
            "section": chunk.section,
            "page_from": chunk.page_from,
            "page_to": chunk.page_to,
            "text": chunk.content,
            "token_estimate": chunk.token_estimate,
            "embedding_model": EMBEDDING_MODEL,
        }
        points.append(
            models.PointStruct(
                # Deterministic, so a forced re-ingest overwrites the same points
                # instead of accumulating near-duplicates.
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"qorvexa-chunk:{sha}:{chunk.ordinal}")),
                vector={DENSE: dense, SPARSE: sparse_vector(indexed_text)},
                payload=payload,
            )
        )

    upsert_chunks(points)
    return FileResult(
        path,
        "ingested",
        chunks=len(chunks),
        pages=report["pages"],
        tokens=sum(c.token_estimate for c in chunks),
        thin_pages=report["thin_pages"],
        document_id=document_id,
    )


def _plan() -> tuple[list[Path], argparse.Namespace, dict]:
    """Argument parsing, path validation and file discovery.

    Deliberately synchronous and separate from the async body below: these are
    all blocking filesystem calls, and a one-shot CLI has no reason to pretend
    otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="file or directory of PDF/DOCX documents")
    parser.add_argument("--scope", choices=("global", "chair"), default="global")
    parser.add_argument("--chair-id", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="re-ingest unchanged files")
    parser.add_argument("--doc-type", choices=("monograph", "detailing_aid", "sop", "brief"))
    parser.add_argument("--brand")
    parser.add_argument("--molecule")
    parser.add_argument(
        "--embeddings-from",
        help="Load vectors from a committed .npz cache instead of calling OpenAI (no key needed).",
    )
    parser.add_argument(
        "--write-embeddings",
        help="Write the freshly computed vectors to this .npz for offline re-ingest.",
    )
    args = parser.parse_args()

    if args.scope == "chair" and args.chair_id is None:
        sys.exit("--scope chair requires --chair-id: a rep-scoped document must have an owner.")
    if args.scope == "global" and args.chair_id is not None:
        sys.exit("--scope global must not carry a --chair-id; it is visible to every rep.")

    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        sys.exit(f"{source} does not exist")

    # The confidential source extract must never be ingested, mirroring the same
    # refusal in etl/export_postgres.py. It is not ours to embed or to ship.
    if any(part == "raw" for part in source.parts):
        sys.exit(
            f"Refusing to ingest from {source} — a path under 'raw/' is the confidential "
            "extract. Only synthetic or explicitly cleared documents may be ingested."
        )

    if not args.embeddings_from and not os.environ.get("OPENAI_API_KEY"):
        sys.exit(
            "OPENAI_API_KEY is not set — embeddings cannot be created. "
            "Pass --embeddings-from to ingest from the committed cache instead."
        )

    files = (
        [source]
        if source.is_file()
        else sorted(p for p in source.rglob("*") if p.suffix.lower() in SUPPORTED)
    )
    if not files:
        sys.exit(f"no PDF or DOCX files under {source}")

    overrides = {
        k: v
        for k, v in (
            ("doc_type", args.doc_type),
            ("brand", args.brand),
            ("molecule", args.molecule),
        )
        if v
    }
    if args.brand and not args.molecule and args.brand in BRANDS:
        overrides["molecule"] = BRANDS[args.brand]["molecule"]

    return files, args, overrides


async def main() -> int:
    files, args, overrides = _plan()

    cache = load_embedding_cache(Path(args.embeddings_from)) if args.embeddings_from else None
    collected: dict[str, list[float]] | None = {} if args.write_embeddings else None

    client = None
    if cache is None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    open_vectors()
    before = count()
    results: list[FileResult] = []
    for path in files:
        try:
            results.append(
                await ingest_file(
                    path,
                    client=client,
                    scope=args.scope,
                    chair_id=args.chair_id,
                    force=args.force,
                    overrides=overrides,
                    embedding_cache=cache,
                    collect_embeddings=collected,
                )
            )
        except Exception as exc:  # noqa: BLE001 — reported per file, never fatal
            results.append(FileResult(path, "failed", detail=str(exc)))

    print(f"\nINGEST REPORT  (scope={args.scope}"
          f"{f', chair_id={args.chair_id}' if args.chair_id else ''})")
    print("=" * 78)
    print(f"  {'file':38} {'status':10} {'pages':>5} {'chunks':>6} {'~tokens':>8}")
    for r in results:
        print(f"  {r.path.name:38} {r.status:10} {r.pages or '-':>5} "
              f"{r.chunks or '-':>6} {r.tokens or '-':>8}")
        if r.detail:
            print(f"      {r.detail}")
        # Surfaced rather than silent: a page with almost no text usually means a
        # table was lost or the page is an image. pypdf is weakest on tables, so
        # this is the signal that would justify escalating to pdfplumber.
        if r.thin_pages:
            print(f"      NOTE thin extraction on page(s) {r.thin_pages} — "
                  f"check the source for tables or scanned content")

    ingested = [r for r in results if r.status == "ingested"]
    tokens = sum(r.tokens for r in ingested)
    print(f"\n  {len(ingested)} ingested, "
          f"{sum(1 for r in results if r.status == 'skipped')} skipped, "
          f"{sum(1 for r in results if r.status == 'failed')} failed")
    print(f"  points in collection: {before} -> {count()}")
    if cache is not None:
        print(f"  vectors loaded from {Path(args.embeddings_from).name} — no API calls made")
    else:
        print(f"  ~{tokens:,} tokens embedded "
              f"(~${tokens / 1_000_000 * USD_PER_MILLION_TOKENS:.4f} at list price)")
    if collected is not None:
        size_kb = write_embedding_cache(Path(args.write_embeddings), collected) / 1024
        print(f"  wrote {len(collected)} vectors to {args.write_embeddings} ({size_kb:.0f} KB)")
    close_vectors()
    return 1 if any(r.status == "failed" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
