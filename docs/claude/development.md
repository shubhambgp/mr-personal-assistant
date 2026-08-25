# How to develop in this repo — the working loop

`backend.md` and `frontend.md` say what must hold; this file says what to DO,
in what order. Follow the loop for every change, however small — the gates are
fast on purpose, so there is no size of change too small to run them.

## The loop

1. **Before touching code.** If the change goes anywhere near `app/tools/`,
   `app/core/security.py`, `app/deps.py`, `app/services/vectors.py`,
   `app/bot/graph.py` or `agenda_tools.py` — re-read
   `security-invariants.md` first, not after. A plausible-looking change can
   silently break an invariant, and the invariant doc exists because several
   already did.
2. **Find where the change belongs before writing it.** The layering decides
   this, not convenience:
   * a new endpoint → `app/api/` (HTTP shaping only, no logic)
   * a new capability for the model → a `ToolProvider` in `app/tools/`,
     registered in `app/registry.py` — never a change to `app/bot/agent.py`
   * persistence or an external client → `app/services/`
   * a new UI surface → `frontend/src/features/<name>/`, one folder
   * a new URL → `frontend/src/lib/routes.ts` first, then use it
   * a new SSE event → the union in `frontend/src/lib/types.ts` first, and let
     the compiler find every render site
3. **Write the change** following the conventions files. Match the surrounding
   code's comment density and idiom — comments here explain *why*, and several
   guard lines that look wrong but are right.
4. **Run the gates.** Every time, before saying "done":

   ```bash
   cd backend
   .venv/bin/ruff check .
   .venv/bin/python -m pytest tests -q      # no DB, ~1s — never skip
   cd ../frontend
   npm run typecheck && npm run lint && npm run format:check && npm run test && npm run build
   ```

   A pre-commit hook (husky + lint-staged, installed by `npm ci` via
   frontend/package.json's `prepare`) runs eslint --fix and the Biome formatter
   on staged files — it is a fast safety net, not a replacement for the gates.
5. **Run the extra gate the change actually needs** (see the matrix below).
6. **Verify like a user, not only like a test.** The eval harness bypasses
   HTTP, so a green gate does not prove a person can send a message
   (ENGINEERING_LOG 16). If the change touches `app/api/chat.py`, the SSE
   contract, auth or uploads — hit the real endpoint with curl or the browser.

## Which extra verification each kind of change needs

| You changed | Also run / do |
|---|---|
| Anything under `backend/` | `pytest tests` + `ruff` (always) |
| SQL tools, guardrails, tenancy, agenda | `pytest evals -q` (needs the loaded DB) |
| Parsing / chunking / embedding | bump `PIPELINE_VERSION`, then `python -m evals.run_rag_eval` and compare recall@5 / MRR to before |
| `app/api/chat.py` or the SSE contract | drive the real HTTP path — login + `/api/chat/stream` with curl, or the browser |
| A new/changed tool | a unit test asserting the *mechanism* (parameter absent, request never sent), not one example |
| A write-capable agenda tool | both directions of `test_exactly_the_write_capable_tools_are_gated` + a `test_write_tools_gated.py` case |
| `etl/manifest.yaml` | regenerate `etl/seed_app.sql` (see DATA.md — needs the parquet sources) |
| An env variable | BOTH templates: `backend/.env.example` (all settings) and, if compose interpolates it, the root `.env.example` + `docker-compose.yml` |
| Frontend styling | remember: an unknown token class fails `npm run lint` — that failure is the safety net, not an obstacle |

## Practices that repeatedly paid for themselves here

* **Reproduce before fixing.** Several "bugs" in this repo's history were the
  wrong process running (a stale server from another checkout), not the code.
  Confirm the failing behaviour against the code you are about to change —
  `readlink /proc/<pid>/cwd` for a server, a curl against the live port.
* **Assert the mechanism, not the example.** A security property is tested by
  the absence of the parameter or the absence of the HTTP request — one passing
  example proves almost nothing (`test_rag_scope.py`,
  `test_write_tools_gated.py` are the models to copy).
* **Fail loudly at startup, not quietly at runtime.** If a config combination
  is invalid, make `config.py` refuse to start. Three validators there exist
  because something once degraded silently instead.
* **When a guard blocks the model, improve the tool, don't loosen the guard.**
* **Clean up after verification.** Smoke-test conversations, uploaded test
  documents and scratch databases get deleted before the work is called done.
* **If the cause was non-obvious, add an ENGINEERING_LOG entry.** The fix is
  code; the reason the code must stay that way is the log.

## What "done" means

All gates green **and** the feature exercised end-to-end once through the real
transport **and** any temporary artefacts removed **and** docs that the change
invalidated (README tables, DATA.md counts, env templates) updated in the same
change. A change that leaves a doc lying is not done.
