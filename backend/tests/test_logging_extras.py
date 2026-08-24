"""No `extra=` key may collide with a reserved LogRecord attribute.

`logging.Logger.makeRecord` raises KeyError for any `extra` key that already
exists on the record, and `thread` is one of them (the OS thread id). So
`log.warning(..., extra={"thread": thread_id})` does not log a slightly wrong
field — it RAISES, every time, at the moment it runs.

That made it worse than a cosmetic bug. It sat inside the handler whose comment
reads "one unreadable thread must not lose the whole list", so the guard defeated
itself: the first unreadable thread crashed the entire triage list. It survived
because the happy path never logs, and it was found by a test about something
else entirely.

A grep-shaped test, like the one that greps the Google package for os.environ:
crude, mechanical, and it fails on a hit.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

#: Everything logging puts on a record itself, plus the three the Formatter adds.
RESERVED = set(logging.LogRecord("n", 20, "p", 1, "m", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def test_no_extra_key_shadows_a_log_record_attribute():
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().split("\n"), 1):
            for block in re.findall(r"extra=\{([^}]*)\}", line):
                for key in re.findall(r'"([A-Za-z_][A-Za-z_0-9]*)"\s*:', block):
                    if key in RESERVED:
                        rel = path.relative_to(APP.parent)
                        offenders.append(f"{rel}:{lineno} uses extra={{{key!r}: ...}}")
    assert not offenders, (
        "these logging calls raise KeyError when they run:\n  "
        + "\n  ".join(offenders)
        + f"\nReserved names: {sorted(RESERVED)}"
    )


def test_the_check_would_actually_catch_it():
    """The guard is the assertion, so prove logging really does refuse."""
    log = logging.getLogger("qorvexa.test.reserved")
    with __import__("pytest").raises(KeyError):
        log.warning("boom", extra={"thread": "th-1"})
    # And the safe name is fine.
    log.warning("fine", extra={"thread_id": "th-1"})
