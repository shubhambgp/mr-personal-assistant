"""Structured JSON logging with a per-request id.

The JD's bar for this is explicit: "If an AI agent can't diagnose a production
issue from your logs, your observability isn't good enough." So every line is
one JSON object on one line, every request carries a correlation id, and the id
also goes back to the client in `X-Request-ID` — so a rep reporting a problem
can quote something that finds the exact request.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
chair_id_var: ContextVar[int | None] = ContextVar("chair_id", default=None)

_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        chair = chair_id_var.get()
        if chair is not None:
            payload["chair_id"] = chair
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Anything passed via logger.info("...", extra={...}) lands here.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # uvicorn ships its own handlers; strip them so everything is JSON.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]
