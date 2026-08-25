"""Append-only audit log of every answered turn.

One asyncio.Queue drained by one background task. Concurrent requests cannot
interleave partial JSONL lines, and no request ever blocks on disk I/O — it
puts a record on the queue and moves on.

`start()` needs a running event loop. Under Chainlit it was called from the
first session's on_chat_start; here it is called once from the FastAPI lifespan
hook, which is both earlier and more predictable.

Note the multi-worker caveat: with several uvicorn workers each process gets its
own writer appending to the same file. Line-buffered appends of complete lines
are atomic enough for that on Linux, but a real deployment should ship these to
a log aggregator rather than a shared file.

REDACTION IS APPLIED HERE, once, rather than at each call site. This file used to
hold only questions about a rep's own synthetic book. It now sits next to a real
mailbox: a mail turn's answer quotes a real doctor's real words, and the drafts
this log records were addressed to real people. So email addresses and
mobile-shaped numbers are replaced on the way in, mail bodies are never passed in
at all (the caller sends a thread id and a digest), and the regulated artefact —
what was actually sent — lives in agenda.outbound_log, a bounded store the
read-only role cannot reach, rather than in a file that gets tarred and shipped
to an aggregator.

Redacting centrally is the same reasoning as defining PII once in the manifest:
a rule applied per call site is a rule someone forgets on the next call site.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

log = logging.getLogger(__name__)

LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "audit.jsonl"


class AuditLogger:
    def __init__(self, path: Path = LOG_PATH) -> None:
        self.path = path
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._handle: TextIO | None = None

    def start(self) -> None:
        if self._task is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Opened here, in sync context, rather than inside the async writer:
            # open() blocks the event loop, and once per process is once too many
            # to hide inside a coroutine.
            self._handle = self.path.open("a", buffering=1)
            self._task = asyncio.create_task(self._run(), name="audit-writer")

    async def stop(self) -> None:
        """Drain what is queued, then stop. Called from the lifespan shutdown."""
        if self._task is None:
            return
        await self._queue.join()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    async def _run(self) -> None:
        assert self._handle is not None  # start() opens it before creating this task
        while True:
            record = await self._queue.get()
            try:
                self._handle.write(json.dumps(record, default=str) + "\n")
            except Exception:  # noqa: BLE001 — one bad write must not kill the writer
                # Without this, a single OSError (disk full, a rotated/closed
                # handle) killed the writer task; every later log() then enqueued
                # into a queue nobody drained, and stop() blocked forever on
                # queue.join() — a hung shutdown, in the compliance-logging
                # component. Log the failure and keep draining. audit finding M-BE1.
                log.warning("audit write failed; record dropped", exc_info=True)
            finally:
                self._queue.task_done()

    async def log(self, **fields) -> None:
        record = {"timestamp": datetime.now(UTC).isoformat(), **redact(fields)}
        await self._queue.put(record)


#: Anything shaped like an email address or an Indian mobile number. Both are
#: PII, and both now reach this log through mail summaries and drafts.
#:
#: `mobile` is already `pii: true` in the manifest and blocked in run_sql; the
#: audit log was the one path that could still carry one, because the model can
#: read a number out of an attached image or a mail body and repeat it.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_MOBILE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")

#: Fields whose whole value is free text a person wrote. Redacted recursively;
#: everything else is left alone so ids, counts and timings stay useful.
_FREE_TEXT_KEYS = frozenset(
    {"question", "answer", "drafted", "review", "body", "subject", "notes", "text"}
)


def scrub(value):
    """Addresses and mobile numbers out of anything — str, dict or list.

    PUBLIC because it has a second caller the moment error tracking is switched
    on: Sentry captures stack-frame locals, and this app's frames hold mail
    bodies. Both paths share this one implementation rather than each carrying
    its own idea of what PII is — the same reasoning that keeps the column-level
    definition in the manifest (CLAUDE.md §1.4). See docs/SENTRY_SETUP.md.
    """
    if isinstance(value, str):
        return _MOBILE.sub("[mobile]", _EMAIL.sub("[email]", value))
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def redact(fields: dict) -> dict:
    """Free-text fields with addresses and mobile numbers removed.

    Exposed (and tested) separately from the writer so the rule can be asserted
    without touching the filesystem.
    """
    return {
        key: (scrub(value) if key in _FREE_TEXT_KEYS else value)
        for key, value in fields.items()
    }


audit_logger = AuditLogger()
