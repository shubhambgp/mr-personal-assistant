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

