# Skills — commands and recipes

## Commands

```bash
# database (once)
sudo -u postgres psql -c "CREATE ROLE qorvexa LOGIN PASSWORD 'qorvexa' CREATEDB;"
sudo -u postgres createdb -O qorvexa qorvexa

# load
cd backend
psql "$DATABASE_URL" -f etl/seed_app.sql                  # data -> schema `app`
psql "$DATABASE_URL" -f etl/chat_history.sql              # chat history -> `public`
psql "$DATABASE_URL" -f etl/agent_schema.sql              # graph checkpoints -> `agent`
psql "$DATABASE_URL" -f etl/agenda_schema.sql             # google + tasks + outbound -> `agenda`
.venv/bin/python -m etl.generate_literature               # corpus -> data/literature
.venv/bin/python -m etl.ingest_docs data/literature --scope global \
    --embeddings-from evals/rag_corpus_vectors.npz        # corpus -> Qdrant, no API key

# run
.venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev                                # proxies /api -> :8000

# check
cd backend && .venv/bin/python -m pytest tests -q         # no DB needed
cd backend && .venv/bin/python -m pytest evals -q         # needs a loaded DB
cd backend && .venv/bin/python -m evals.run_rag_eval      # retrieval: offline, no key
cd backend && .venv/bin/ruff check .
cd frontend && npm run typecheck && npm run lint && npm run build
```

## Recipe: add a column to the data model

1. Add it to the table's entry in `etl/manifest.yaml` (with `pii: true` if it
   is PII — that single flag drives the CTE exclusion and the denylist).
2. Re-run `.venv/bin/python -m etl.load_postgres` (needs the parquet sources,
   which are not in the repo) and re-cut `etl/seed_app.sql` — see DATA.md.
3. Nothing else. `schema.py`, the scoped CTEs and the denylist all derive from
   the manifest. Hand-editing Postgres instead means `run_sql` rejects queries
   against the new column.

## Recipe: add a read-only tool

1. Implement a `ToolProvider` in `app/tools/` (or extend an existing one) —
   the handler closes over `RepContext`, returns errors as
   `json.dumps({"error": ...})`, and never accepts identity/scope parameters
   (`ToolRegistry.build()` raises if the JSON Schema declares one).
2. Register it in `app/registry.py`. Do not touch `app/bot/agent.py`.
3. If it queries Postgres it uses `DATABASE_URL_RO`; if it needs a write, the
   handler calls a function in `app/services/` — the service owns the pool.
4. Add a unit test in `tests/`; if it has a security property, assert the
   mechanism (parameter absent, request never sent), not one example.

