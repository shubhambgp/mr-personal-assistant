# Testing and evals

## The three tiers

| Tier | Command (from `backend/`) | Needs |
|---|---|---|
| Unit tests | `.venv/bin/python -m pytest tests -q` | nothing — ~1 second |
| Guardrail evals | `.venv/bin/python -m pytest evals -q` | a loaded database |
| Retrieval eval | `.venv/bin/python -m evals.run_rag_eval` | offline, no API key |
| LLM eval | `.venv/bin/python -m evals.run_eval` | database + `OPENAI_API_KEY` |

Frontend: `npm run typecheck && npm run lint && npm run build`.

## Rules

* Run `pytest tests` before you finish any backend change. It needs no
  database and takes about a second. There is no excuse to skip it.
* Security properties get a test that asserts the *mechanism*, not the
  behaviour of one example — e.g. `test_rag_scope.py` asserts the filter
  parameter does not exist, `test_write_tools_gated.py` asserts the HTTP
  request never happened on a MockTransport.
* A green eval gate is not the same claim as "a user can send a message" — the
  eval harness drives `app/bot/` directly and bypasses HTTP entirely. A
  transport bug slipped through exactly that way (ENGINEERING_LOG 16). If you
  touch `app/api/chat.py` or the SSE contract, check the browser path too.
* Natural-language evals can hide structural leaks: `pii_not_accessible`
  passed while `SELECT *` returned the mobile column, because the eval only
  ever *asked* in English (ENGINEERING_LOG 2). When testing a guard, drive the
  layer under the language, not the language.
* When a guard rejects something the model wanted to do, the fix is usually to
  give the model a better tool — not to loosen the guard.

## CI

`.github/workflows/ci.yml` runs: ruff + unit tests, ETL load + guardrail evals
against the loaded database, dataset verification, offline retrieval eval
(recall@5 / MRR from committed vectors), frontend typecheck/lint/build, a
confidentiality scan (hash-based, sees `etl/check_no_vendor_terms.py`), and —
on push to main only, where the secret exists — the LLM eval gate.
