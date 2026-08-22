# Security invariants — do not break these

This is not style guidance; it is a list of invariants that a plausible-looking
change can silently break. If you touch `app/tools/`, `app/core/security.py`,
`app/deps.py` or `app/services/vectors.py`, re-read this file before you finish.

---

## 1.1 `chair_id` comes from the verified JWT. Only.

A rep may only ever see their own book. The entire mechanism is:

```
JWT (signed)  ->  decode_token()  ->  RepContext(frozen)  ->  closed over by every tool
```

* `app/core/security.py:decode_token` is the **only** place claims are read.
* `app/deps.py:current_rep` is the **only** place a `RepContext` is constructed.
* Never accept a `chair_id`, `rep_id` or `rep_code` from a request body, query
  string, header, or tool argument. If you find yourself wanting to, the answer
  is that the caller already has a token that says who they are.

## 1.2 No tool may take a scope parameter

Tools close over `RepContext`. They do not accept identity as an argument,
because the model composes those arguments and the model is not the authority on
who is asking.

`ToolRegistry.build()` enforces this mechanically — it raises if any spec's
JSON Schema declares `chair_id`/`rep_id`/`rep_code`. Do not remove that check.
`tests/test_tool_registry.py::test_no_tool_accepts_a_scope_parameter` covers it.

## 1.3 Never `SELECT *` in a scoped CTE

This is a fixed bug, not a preference.

`run_sql`'s PII guard is a regex over the model's **query text**. A query like
`SELECT * FROM my_doctors` never types the word `mobile`, so it passed the guard
— and returned the column in the result rows anyway. The natural-language eval
(`pii_not_accessible`) kept passing the whole time, because it only ever *asked*
in English.

The fix is structural: `scoped_ctes()` enumerates non-PII columns explicitly,
from the manifest. Keep it that way.

## 1.4 PII is defined in one place

`etl/manifest.yaml`, via `pii: true`. Read it through
`app/bot/schema.py:pii_columns()`. Never hardcode a list of PII column names —
a second copy can only drift, and the copy that drifts is the one that leaks.

## 1.5 The tool layer connects read-only

Two roles, two DSNs:

| DSN | Role | Used by |
|---|---|---|
| `DATABASE_URL` | owner | ETL, auth lookups, chat-history writes |
| `DATABASE_URL_RO` | `qorvexa_ro`, `SELECT` only | everything the agent touches |

The regex guards in `sql_tools.py` are a filter. The read-only role is the
boundary. Do not point the tool layer at the owner DSN "just for now", and do
not grant `qorvexa_ro` anything beyond `SELECT`.

**The agenda's write path, and why it does not break this.** Sending mail and
adding a task are writes, and a write-capable tool cannot honour the letter of
this rule. It honours the purpose: the *tool handler* never touches a pool. It
calls `app/services/agenda.py`, which owns the connection and writes through the
owner pool exactly as `services/conversations.py` already does for chat history —
the same pattern, and the same reasoning. What matters is that `qorvexa_ro`, the
role model-composed SQL runs as, has no privileges in the `agenda` schema at all.
A dedicated third role narrowed to those two tables is the stronger version and
is deliberately deferred; it would add a DSN, a pool and two CI changes for a
narrowing `evals/test_agenda_guardrails.py` already asserts.

## 1.6 The data model is not user-facing

Never put the table/column listing back into `build_instructions()`. It lives in
the `run_sql` tool description, and that placement is load-bearing: when the
listing was in the system prompt, the model read it as general knowledge and
recited it — "list down all the tables" returned a bullet list of every scoped
alias, "show me the schema of my_reps" returned a column-and-type table.

The rep is a medical representative, not a DBA. Questions about tables, columns,
schemas or counts are really questions about *capability*, and the rules in
`SYSTEM_RULES` carry the answer to give instead. Reframe, do not merely refuse —
"what data do you have about me?" should return their name, code, cluster and
current metrics, not a list of column names.

`guardrails.check_internal_disclosure()` measures whether this holds, and
`tests/test_internal_disclosure.py::test_the_system_prompt_no_longer_carries_the_schema`
guards the root cause directly.

## 1.7 Any new tool provider follows the same rules

Adding RAG or an MCP server means adding a `ToolProvider` to
`app/registry.py`. It does **not** mean touching `app/bot/agent.py`.

* **RAG:** retrieval must filter on `ctx.chair_id` through the `documents`
  table. A vector search with no tenant predicate returns another rep's
  documents ranked by cosine distance — and the results still look plausible,
  which is why this will not show up in casual testing.
* **Agenda (Gmail/Calendar):** the mailbox is never a tool parameter and never a
  client input. It is resolved server-side from `chair_id` — a verified token
  claim — through exactly one function, `services/agenda.connection()`, and
  attached to `RepContext` by `deps.agenda_rep`. Both `chair_id` **and**
  `rep_code` are matched, and a mismatch **deletes** the row: a field force
  reassigns a rep code when someone leaves, and keyed on one identifier the
  replacement would inherit the previous rep's mailbox.

  It is stored server-side rather than carried in the JWT **deliberately**, and
  this is a considered deviation from the older wording ("populated from the
  verified token"): a claim inside an 8-hour token cannot be withdrawn, so a rep
  who disconnects at 09:05 would still be asserting the address at 17:00, and
  there is no refresh rotation or revocation list. A stored connection is revoked
  in one statement. Both properties this rule cares about survive — the model
  cannot name a mailbox, and the mailbox is not client-supplied.

  `_FORBIDDEN_PARAMS` in `app/tools/base.py` enforces it mechanically, the same
  way the `chair_id` rule already is, and the check is **recursive** because
  `create_event` takes an array of attendees and a forbidden name one level down
  used to pass. `to` is deliberately not on that list: the recipient is what the
  human approves, and it is controlled instead by deriving it from the thread
  server-side plus a correspondent allowlist.
* **MCP:** still the empty seam. Remote tool descriptions and results are
  third-party text that reaches the model: treat them as data, never as
  instructions. The agenda is its own provider, not an MCP server.
* **Mail bodies are the widest untrusted surface in the app.** A retrieved PDF at
  least had to be ingested by someone; anyone who knows the rep's address can put
  text in front of the model. The "data, not instructions" rule therefore lives in
  the tool *description*, where a mail body cannot reach it — never only in the
  payload.
* Name collisions are a hard error by design. A remote server exposing
  `get_daily_plan` must fail at startup, not silently shadow ours. Do not
  "fix" that with last-one-wins.

## 1.8 LangGraph: three rules the graph must keep

The agent core is a `StateGraph` (`app/bot/graph.py`). It exists for one reason —
human-in-the-loop — and it brought one new class of risk with it.

* **`RepContext` never goes in graph state.** State is checkpointed and
  resumable; identity must be re-derived from the verified JWT on *every* entry,
  including a HITL resume. It travels in `config["configurable"]["rep"]`, and
  tools close over it exactly as before. A resumed thread carrying a persisted
  identity is a resumed thread that can run as the wrong rep.
* **`thread_id` is never client-supplied.** It is the conversation uuid returned
  by `conversations.get_or_create`, which filters on `(id, chair_id)` and creates
  a fresh row when the id is not the caller's. That is the only thing stopping
  rep B resuming rep A's transcript, and
  `evals/test_guardrails.py::test_a_foreign_conversation_id_never_becomes_the_graph_thread`
  is what keeps it true.
* **The checkpoint tables are never granted to `qorvexa_ro`.** They hold every
  rep's full message history. `AsyncPostgresSaver` creates them *unqualified*, so
  with the app's normal `search_path=app,public` they would have landed in `app`
  — which the ETL drops on every load **and** which auto-grants SELECT to the
  read-only role via `ALTER DEFAULT PRIVILEGES`. `run_sql`'s denylist is built
  from the manifest and would not have known their names. Hence `etl/agent_schema.sql`,
  a dedicated `agent` schema, and a checkpointer connection pinned to
  `search_path=agent`. Do not add a GRANT there. See ENGINEERING_LOG 15.

**Tools are adapted, not rewritten.** `ToolRegistry.build()` still runs first and
still mechanically rejects a `chair_id` parameter; `app/bot/tool_adapter.py`
converts `ToolSpec` to `StructuredTool` downstream of that check. Do not
"simplify" this by turning the tool providers into `@tool` functions — the
invariant is the whole security model, and re-expressing it inside someone
else's abstraction is how such things get quietly lost.

