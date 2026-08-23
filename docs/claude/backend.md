# Backend conventions

Applies to everything under `backend/`.

## Python

* Python 3.12+, `from __future__ import annotations`, full type hints on
  anything public. `ruff` must pass (`.venv/bin/ruff check .`).
* Do not add a dependency to work around something the standard library does.
* Prefer failing loudly at load/startup over degrading quietly at runtime.
  Three of the checks in this repo exist because something failed quietly once.

## Errors

* **Errors reaching the model** are returned as `json.dumps({"error": ...})`,
  never raised. A raised exception becomes an opaque tool failure; a returned
  error is something the model can read and explain to the rep.
* **Errors reaching the user** go through `chat.py:_explain()`. Never leak an
  internal exception string into the SSE stream.

## SQL

* `%s` placeholders, always. The only value ever interpolated into SQL text is
  `chair_id`, and only after `int()`.
* Never hand-edit the database schema — change `etl/manifest.yaml` and re-run
  the loader (see architecture.md, "The manifest is the source of truth").
* New tables holding anything sensitive get their own schema, never `app` —
  `app` is dropped on every ETL load and auto-grants SELECT to `qorvexa_ro`.

## Layering

* `app/api/` is HTTP only — request parsing, auth dependency, response shaping.
  No business logic.
* `app/bot/` is transport-agnostic. It must never import from `app/api/`.
* `app/services/` owns persistence and external clients. Tool handlers call
  services; they never touch a connection pool directly.
* Adding a tool provider means adding to `app/registry.py`, not touching
  `app/bot/agent.py`.

## Logging

* Never log a mail body, an address, or a mobile number. `AuditLogger.log`
  redacts in one place — route free-text through it, do not log around it.
* `extra=` keys must not shadow a `LogRecord` attribute (`thread`, `module`,
  `name`, ...) — `logging` raises on the collision instead of logging.
  `tests/test_logging_extras.py` greps for it.
