"""Health and metrics.

/api/health is unauthenticated and cheap — a load balancer calls it constantly,
so it must not do real work beyond one trivial query per pool.

/api/metrics requires a login. It is operational data, not public.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from ..bot import db, schema
from ..config import settings
from ..core.metrics import metrics
from ..deps import CurrentRep
from ..services import vectors

router = APIRouter(prefix="/api", tags=["ops"])
log = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict:
    checks: dict[str, str] = {}
    healthy = True

    for label, pool_fn in (("db_ro", db.ro_pool), ("db_rw", db.rw_pool)):
        try:
            with pool_fn().connection() as conn:
                conn.execute("SELECT 1")
            checks[label] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks[label] = f"error: {type(exc).__name__}"
            healthy = False
            log.error("health check failed", extra={"check": label})

    # Qdrant is the ONLY document store (there is no SQL backstop), so an LB
    # seeing "ok" while it is down means retrieval is silently broken. A
    # collection-existence check is a connectivity probe without the cost of a
    # full count. See audit finding M-OPS8. (The graph checkpointer shares the same
    # Postgres the db_rw probe already covers, so it needs no separate check.)
    try:
        vectors.vectors().collection_exists(vectors.COLLECTION)
        checks["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["qdrant"] = f"error: {type(exc).__name__}"
        healthy = False
        log.error("health check failed", extra={"check": "qdrant"})

    # Presence only — never call the model from a health check.
    checks["openai_key"] = "set" if settings.openai_api_key else "missing"
    if not settings.openai_api_key:
        healthy = False

    try:
        checks["manifest_relations"] = str(len(schema.base_relations()))
    except Exception as exc:  # noqa: BLE001
        checks["manifest_relations"] = f"error: {type(exc).__name__}"
        healthy = False

    return {
        "status": "ok" if healthy else "degraded",
        "environment": settings.environment,
        "checks": checks,
    }


@router.get("/vintage")
def vintage(rep: CurrentRep) -> dict:
    del rep
    rows = db.data_vintage()
    return {
        "tables": [
            {"table": t, "max_load_date": v, "row_count": n} for t, v, n in rows
        ],
        "summary": ", ".join(sorted({v for _t, v, _n in rows})) or "unknown",
    }


@router.get("/metrics")
def read_metrics(rep: CurrentRep) -> dict:
    del rep
    return metrics.snapshot()
