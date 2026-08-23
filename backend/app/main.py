"""FastAPI application: lifespan, middleware, routers.

Startup order matters: pools open before the first request, and the audit
writer starts inside the running loop (it needs one to create its task).
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import agenda, auth, chat, conversations, documents, health
from .bootstrap import close_resources, open_resources
from .config import settings
from .core.logging import configure_logging, new_request_id, request_id_var
from .core.metrics import metrics
from .registry import registry

configure_logging(settings.log_level)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)
            log.info("sentry enabled")
        except ImportError:
            log.warning("SENTRY_DSN set but sentry-sdk is not installed")

    # One shared entry point, so the eval harness cannot drift from the app.
    # See app/bootstrap.py for why that matters.
    opened = await open_resources()

    log.info(
        "startup complete",
        extra={
            "tool_providers": registry.provider_names,
            "env": settings.environment,
            **opened,
        },
    )
    try:
        yield
    finally:
        await close_resources()
        log.info("shutdown complete")


#: The interactive docs enumerate every endpoint and schema — useful in
#: development, an unauthenticated map of the attack surface in production.
_IS_PRODUCTION = settings.environment == "production"

app = FastAPI(
    title="MR Personal Assistant",
    description="Field-force assistant for pharmaceutical medical representatives.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if _IS_PRODUCTION else "/docs",
    redoc_url=None if _IS_PRODUCTION else "/redoc",
    openapi_url=None if _IS_PRODUCTION else "/openapi.json",
)

# Credentialed CORS requires explicit origins — "*" is rejected by browsers when
# allow_credentials is on, and the session cookie is the whole auth mechanism.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def observability(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    request_id_var.set(request_id)
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        metrics.incr("unhandled_errors")
        log.exception(
            "unhandled error",
            extra={"path": request.url.path, "method": request.method},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    # Health checks are high-frequency and low-information; logging them buries
    # everything else.
    if request.url.path != "/api/health":
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 1),
            },
        )
    return response


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(documents.router)
app.include_router(agenda.router)
