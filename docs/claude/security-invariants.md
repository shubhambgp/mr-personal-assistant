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

