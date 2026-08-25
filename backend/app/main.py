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


# ── error tracking: the redactor, ready to switch on ────────────────────────
# Sentry is wired but OFF: `sentry-sdk` is commented out in requirements.txt and
# SENTRY_DSN is empty, so a fresh checkout sends nothing anywhere. To turn it on,
# uncomment the pin, set the DSN, and uncomment this function plus the two
# `before_send` / `traces_sample_rate` lines in the lifespan below.
#
# It exists as a sample rather than live code because there is nothing to send
# errors to yet — but it is written out in full, because the interesting part is
# not the wiring, it is that events must be redacted with the SAME function the
# audit log uses. A second copy of "what counts as PII" is a copy that drifts.
# Full reasoning: docs/SENTRY_SETUP.md
#
# from .bot.audit import scrub
# from .core.logging import chair_id_var, request_id_var
#
# def _before_send(event, hint):
#     """Redact, then correlate. Runs on every event before it leaves us."""
#     del hint
#     # Same regexes as the audit log — addresses and mobile-shaped numbers out.
#     event = scrub(event)
#     event.setdefault("tags", {})["request_id"] = request_id_var.get()
#     chair = chair_id_var.get()
#     if chair is not None:
#         # An integer, never rep_name: the tag has to identify the rep to US
#         # without naming a person to a third party.
#         event["tags"]["chair_id"] = str(chair)
#     return event


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.environment,
                # Both of these are ACTIVE safety settings, not tuning.
                #
                # send_default_pii attaches cookies, headers and the request
                # body — and the session cookie IS the auth token here. It
                # defaults to False; it is written out so nobody "enables the
                # useful context" without reading this.
                send_default_pii=False,
                # include_local_variables defaults to TRUE, and that default is
                # wrong for this app: stack frames in services/agenda hold
                # `body`, `subject` and `thread_text`, so an error inside
                # send_mail would ship a draft addressed to a prescriber to a
                # third party — exactly what CLAUDE.md §1.10 forbids the audit
                # log from doing.
                include_local_variables=False,
                # Uncomment with _before_send below. See docs/SENTRY_SETUP.md.
                # before_send=_before_send,
                # A turn is 10-60s and ~97.5% of it is the model, so tracing
                # every one buys noise and cost rather than signal.
                # traces_sample_rate=0.05,
            )
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
