"""Rep-uploaded documents: the second entry point into the ingest pipeline.

A rep's own PDF or Word file goes through exactly the same parser, chunker,
embedder and metadata inference as the shared company library — `etl.ingest_docs`
is imported rather than reimplemented, because two ingest paths would eventually
chunk differently and only one of them would be the one the eval measures.

TENANCY. An upload is always `scope='chair'` with the chair_id taken from the
verified JWT. There is no request field for it, and none should be added: the
whole retrieval boundary is the payload filter built from RepContext.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from ..core.security import RateLimiter
from ..deps import CurrentRep
from ..services import vectors
from ..services.openai_client import get_client

router = APIRouter(prefix="/api/documents", tags=["documents"])
log = logging.getLogger(__name__)

#: Ingestion is synchronous, so these caps are what keep a request bounded. A
#: 202-plus-status-polling design is the honest upgrade and is in the README's
#: known limitations rather than pretended.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGES = 120
ALLOWED_SUFFIXES = {".pdf", ".docx"}

#: Per-rep: each upload costs parsing plus an embedding call per chunk, so this
#: is a spend control, not a UX one. A rep building a library uploads a handful
#: of files; a script uploads hundreds (audit finding M-SEC5). In-process, same
#: single-worker caveat as the login limiter.
_upload_limiter = RateLimiter(max_attempts=12, window_seconds=600)


@router.get("")
def list_documents(rep: CurrentRep) -> list[dict]:
    """What this rep can retrieve from: the shared library plus their own uploads."""
    return vectors.list_documents(rep)


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(rep: CurrentRep, file: UploadFile = File(...)) -> dict:
    key = str(rep.chair_id)
    if not _upload_limiter.check(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many uploads in a short time. Wait a few minutes and try again.",
            headers={"Retry-After": str(_upload_limiter.retry_after(key))},
        )

    name = Path(file.filename or "document").name
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Only {', '.join(sorted(ALLOWED_SUFFIXES))} files can be added.",
        )

    # Bounded to one byte over the cap, never a bare read(): the whole body used
    # to be pulled into RAM before the size check, so an oversized upload was a
    # worker-memory spike before its 413 — the same bug chat.py's image path
    # fixed (audit finding M-SEC4). One extra byte is exactly enough to tell
    # "at the limit" from "over it"; the exact size is deliberately unknown.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        mib = 1024 * 1024
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"{name} is larger than the {MAX_UPLOAD_BYTES // mib} MB limit.",
        )

    from etl.ingest_docs import ingest_file

    # A real file on disk, because the parsers take paths and a NamedTemporaryFile
    # is the honest way to bridge that without teaching them about streams.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_bytes(data)
        try:
            result = await ingest_file(
                path,
                client=get_client(),
                scope="chair",
                chair_id=rep.chair_id,
                overrides={},
            )
        except Exception as exc:  # noqa: BLE001 — turned into a client message
            # NB: not `filename` — that is a reserved LogRecord attribute and
            # logging raises KeyError rather than ignoring it.
            log.exception("document ingest failed", extra={"document": name})
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="That file could not be read. A text-based PDF or Word file works best.",
            ) from exc

    if result.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"No text could be extracted from {name}. "
                "Scanned or image-only documents are not supported."
            ),
        )
    if result.pages > MAX_PAGES:
        # Ingested already, so ACTUALLY remove it. This comment used to stand
        # over code that never deleted anything — the rep was told the file was
        # rejected while its full content stayed retrievable in their corpus
        # (audit finding M-BE6). document_id is server-derived, never client input.
        if result.document_id:
            vectors.delete_document(result.document_id)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"{name} has {result.pages} pages (limit {MAX_PAGES}).",
        )

    log.info(
        "document ingested",
        extra={
            "document": name,
            "chunks": result.chunks,
            "pages": result.pages,
            "status": result.status,
        },
    )
    return {
        "filename": name,
        "status": result.status,
        "pages": result.pages,
        "chunks": result.chunks,
        "detail": result.detail,
    }
