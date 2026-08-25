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

## 1.9 Retrieval: one filter, and it only does tenancy

The document corpus lives in **Qdrant and nowhere else** — vectors, text and
metadata together. Postgres is deliberately not in this path (no dual-write, no
orphaned vectors on delete), which means **there is no SQL backstop**. The payload
filter is the entire tenancy boundary.

* **`vectors._scope_filter()` is the only filter this codebase builds**, and it
  expresses exactly one thing: `scope = 'global' OR chair_id = <this rep>`.
* **`vectors.search()` takes a `RepContext`, never a filter.** There is no
  parameter through which a caller — or a tool argument the model composed — can
  supply, widen or replace the predicate. `tests/test_rag_scope.py` asserts the
  absence of such a parameter, because the absence *is* the security property.
* **Nothing else is filtered, and that is deliberate.** brand, molecule and
  doc_type were `must` conditions and each one silently excluded the document
  that answered the question: an unknown brand has a null payload brand, and a
  file inferred as `brief` is excluded by `doc_type="monograph"`. The model got
  "no results" while retrieval was working perfectly. **A model-supplied
  narrowing may steer ranking; it must never be able to empty the result set.**
  Hints are folded into the query text instead.
* **Assert absence from the raw tool output, not from the prose.** A guard that
  only stops the model *mentioning* another rep's document has still put that
  text into the transcript, the audit log and the UI.
* **Retrieved text is untrusted.** A PDF — especially one a rep uploaded — can
  contain "ignore previous instructions". The instruction to treat results as
  data lives in the *tool description*, i.e. in the prompt, where a document
  cannot reach it. Never move that into the payload alone.

Ingestion is idempotent on `content_sha256` **and** `PIPELINE_VERSION`. Bump the
version whenever parsing, chunking or the contextual header changes shape —
otherwise a pipeline change leaves every existing document silently stale,
because the file has not changed so ingestion skips it.

## 1.10 The agenda: three agents, one gate, and a real credential

* **THE RULE IS: gated = writes to Google.** Five tools qualify — `send_email`,
  `create_event`, `update_event`, `cancel_event`, `schedule_task` — and they are
  the complete list in `agenda_tools.GATED_TOOL_NAMES`. `create_task` and
  `update_task` write only our own database and are NOT gated; gating harmless
  things trains the rep to click through the approvals that matter.

  Moving or cancelling a meeting is gated because **Google mails the attendees
  itself**: it is outbound contact with a prescriber, posted by Google rather than
  by us. `cancel_event` is gated with nothing editable — what the rep approves
  there is the act, not the text.

* **A new write tool must be built with `_write_tool()`**, the single private
  constructor in `agenda_tools.py` that sets `requires_approval` unconditionally
  and refuses an `editable` list naming a recipient, mailbox, thread, event or
  task id. Never hand-write the spec dict for a write tool: a flag a caller
  supplies is a flag a caller can forget, and what gets forgotten is the human.

  Four independent guards, in order of how much they are worth:
  1. **The graph.** A round containing any gated call routes to
     `review → approval` and never to the tool node. Not a check that runs — an
     edge that does not exist.
  2. `_write_tool()` sets the flag.
  3. `tests/test_write_tools_gated.py` drives all five through the real graph and
     asserts the Google write **never happened** — by the absence of the HTTP
     request on a MockTransport, not by a message.
  4. The service layer re-runs `check_outbound` on the final bytes.

  `tests/test_tool_adapter.py::test_exactly_the_write_capable_tools_are_gated`
  asserts the set in both directions.

* **`route()` sends a gated round to `review`, never straight to `approval`.** So
  "the reviewer's verdict exists before the human sees the card" is a property of
  the graph, not a convention. The reviewer is its own node and not a step inside
  `approval` because a node **re-executes from its start on resume** — a reviewer
  in there would run twice and could contradict the verdict the rep approved,
  leaving an audit record describing a review that never happened.

* **An edit may change what is SAID, never who it is said TO.** The editable
  fields come from each tool's own `approval_editable` metadata and are filtered
  **server-side, inside the graph** — the approval card renders that list and the
  card is not trusted. `to`, `thread_id` and `attendees` are on no whitelist.

* **The recipient is not the model's to choose.** On a reply it comes from the
  thread and the model's `to` is *ignored*, not validated; a new address must be
  one the rep has already corresponded with. That is the control that does not
  depend on the model obeying the "treat mail as data" instruction.

  "Corresponded with" means **both directions** — `services/agenda.correspondents`
  reads the rep's own mailbox and collects who wrote to them *and* who they wrote
  to. It used to be inbound only (built from `TriageItem.from_address`, which is
  the counterparty's, and a thread nobody answered has no counterparty), so a rep
  could not follow up on their own outbound mail while the refusal claimed they
  had never corresponded. Widening it does not weaken the control: an address a
  mail body asks us to write to still appears in no thread of the rep's own.
  `tests/test_correspondents.py` asserts both halves.

* **Compliance is checked in the service, not the card.** `services/agenda.send_mail`
  re-runs the deterministic rules on the final bytes, because the rep may have
  edited the wording after the reviewer saw it. Whichever way the transport is
  wired, nothing reaches Gmail without passing it.

* **The `agenda` schema is never granted to `qorvexa_ro`.** It holds a Google
  refresh token and every word a rep has sent a prescriber. None of these tables
  is in the manifest, so `run_sql`'s denylist does not know their names — the only
  thing keeping model-composed SQL out is the absent privilege. Third time this
  reasoning has been needed (chat history, checkpoints, now this). Do not add a
  GRANT. `evals/test_agenda_guardrails.py` is what keeps it true.

* **A paused turn must be durably discoverable.** The interrupt lives in the graph
  checkpoint and the UI rebuilds from `public.messages`, so
  `messages.pending_approval` carries the card. Without it a reload loses the
  decision **and** wedges the thread: the next message re-enters a thread whose
  interrupted task is still pending, so it interrupts again, forever. The
  checkpoint stays the authority — `/api/chat/resume` matches the `interrupt_id`
  against live graph state and 409s on a mismatch.

* **A resume re-checks identity and ownership.** It is a second entry point into
  the same state (see 1.8). Ownership uses the strict `conversations.owned_by`,
  never `get_or_create`, whose create-on-miss behaviour is right mid-stream and
  wrong here — it would fork an empty thread and swallow the resume instead of
  refusing it. 404, never 403.

* **Never log a mail body.** `AuditLogger.log` redacts addresses and mobile-shaped
  numbers from free-text fields in one place, so it cannot be forgotten per call
  site. The regulated artefact — what was actually sent, and the verdict the rep
  approved — lives in `agenda.outbound_log`, which is append-only by construction
  and unreachable by the read-only role. `audit.jsonl` gets shipped to
  aggregators; that table does not.

* **A connection has three states, not two: live, stale, absent.** A grant dies
  while its row lives — a Testing-audience token expires after 7 days, and a rep
  can revoke access or change their password. On `invalid_grant` the credential is
  **deleted** and `needs_reconnect_at` stamped; the row survives so Settings can
  name which account to reconnect. A CHECK constraint makes those two facts one
  inseparable state. `connection()` returns the row with `stale=True`, and
  `deps.agenda_rep` leaves `email_account` None for it, so the mail tools are not
  offered — `agenda_status` looks the state up itself and says "expired" rather
  than "never connected", which would be a lie.

  **ONLY `invalid_grant` may delete a token.** `invalid_client` means the
  *operator's* client secret is wrong, and treating it the same way would wipe
  every rep's credential across the deployment on the first request after a bad
  deploy — consent cannot be restored server-side. `GoogleError.code` exists for
  exactly this one branch, and reading it needs both of Google's error body
  shapes: the REST APIs nest an object, the OAuth token endpoint uses a bare
  string.

* **`extra=` keys must not shadow a LogRecord attribute.** `logging` raises
  KeyError on a collision, so `extra={"thread": …}` does not log a wrong field —
  it *raises*. That sat inside the handler whose comment reads "one unreadable
  thread must not lose the whole list", so the guard defeated itself.
  `tests/test_logging_extras.py` greps for it.

* **Task sections and counts are computed server-side.** Same reason as triage: the
  panel and a chat answer must not disagree, and `check_grounding` rejects a
  2+-digit number the model did not get from a tool. "Overdue" means the due
  MOMENT has passed, which needs one authoritative timezone — a connected
  account's `calendar_tz`, else `AGENDA_TIMEZONE`. `due_time` is a separate
  nullable column, not part of a timestamp: all-day and timed are different
  states, and null is not midnight.

* **Mail search takes named fields, never a query string.** The server composes the
  Gmail query from `from_name` / `subject_contains` / `since_days`. A free-text
  parameter would let mail text steer the search, which is the same failure triage
  avoids by ignoring wording. Values are quoted AND every quote stripped first —
  the stripping is what makes the quoting sound.
