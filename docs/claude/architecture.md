# Architecture, and why

```
frontend/  React 19 + TS + Tailwind v4, Vite — feature-first
  app/                     shell, providers, layout. Owns nothing domain-ish.
  features/{auth,chat,conversations,agenda,settings,library}/
                           a feature is ONE folder: components, hooks and its
                           own state together. Adding RAG or email-MCP UI means
                           adding features/<name>/ and touching nothing else.
  components/ui/           shared primitives only. If it is chat-specific it
                           does not belong here.
  lib/                     cx, format, theme, api, types
  styles/theme.css         SINGLE SOURCE OF TRUTH for design tokens
  features/chat/useChatStream.ts
                           fetch() + ReadableStream SSE reader (EventSource
                           cannot POST, and we need multipart for images)
backend/
  app/api/       HTTP only. No business logic.
  app/tools/     ToolProvider implementations + the registry
  app/bot/       transport-agnostic agent core. Knows nothing about HTTP.
    graph.py       the LangGraph StateGraph + the turn runner
    agent.py       system prompt, build_instructions, TurnResult (no loop)
    tool_adapter.py ToolSpec -> StructuredTool, downstream of the registry check
    checkpointer.py durable graph state, pinned to the `agent` schema
  app/services/  persistence and external clients
    vectors.py     Qdrant: the ONLY store for the document corpus. Holds the
                   single tenancy filter — see security invariant 1.9 before
                   touching it.
  app/integrations/google/  Gmail, Calendar, OAuth — HTTP clients only
  etl/           manifest-driven parquet -> Postgres
    ingest_docs.py PDF/DOCX -> parse -> section-aware chunk -> embed -> Qdrant.
                   Imported by POST /api/documents, not reimplemented there.
```

## The transport boundary

`app/bot/` has no idea a web server exists. It emits events through async
callbacks and lets the caller decide what to do with them. That is why it
survived being moved off a completely different UI framework without changes,
then onto LangGraph without the API layer or the frontend changing, and it is
why the eval harness can drive it directly with no server running. Keep that
boundary.

But note what that boundary costs: because the eval harness bypasses HTTP, it
cannot catch a bug in the transport. One slipped through exactly that way — see
ENGINEERING_LOG 16. A green eval gate is not the same claim as "a user can send
a message"; check both.

## The manifest is the source of truth

`backend/etl/manifest.yaml` defines the schema. Three things derive from it:

1. `etl/load_postgres.py` — DDL, the load, and every cleaning transform
   (the committed dataset is `etl/seed_app.sql`, cut from its output)
2. `app/bot/schema.py` — `queryable_columns()`, `pii_columns()`, `glossary_text()`
3. `app/tools/sql_tools.py` — the `run_sql` scoped CTEs and the denylist

**Do not hand-edit the database schema.** Change the manifest and re-run the
loader. Adding a column to Postgres directly means the model is never told it
exists and `run_sql` will reject queries against it.

Two pairs that must stay in sync, and neither has a compiler to catch it:

* A view's `columns:` list and its `sql:`. The loader asserts they match at load
  time and exits if they do not — that check is the safety net; keep it.
* `resolve.py:normalize_name()` and `load_postgres.py:NAME_NORM_SQL`. The fuzzy
  matcher compares its Python normalisation against the value the ETL stored. If
  you change one, change both.

