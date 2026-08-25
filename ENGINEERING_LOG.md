# Engineering Log

> A public record of engineering failures, investigations, fixes, and lessons learned.
>
> This document intentionally describes security and reliability findings at a level
> useful for engineering discussion without publishing credentials, production
> connection details, internal identifiers, or directly reusable exploit payloads.

The interesting part of building this system was not only the features. It was the
unexpected behaviour found while testing, measuring, and trying to break the system.

---

Bugs that were found, what each one actually was, and what changed because of
it. Kept because the interesting part of building this was not the features.

Three of these were found by going looking, not by something breaking.

---

## 1. Arbitrary file read through the query tool

**Found:** while probing my own `run_sql` guards, asking what they *didn't* cover.

The guards were built around table names — a denylist, plus scoped views. The
embedded engine in use at the time also had table *functions*, which never name
a table:

```text
Example exploit omitted from the public engineering log; the important finding was that table-name guards did not cover non-table file access.
```

Every one passed every guard, because there was no table name to match. The
second is the bad one: it re-read the source files directly, bypassing rep
scoping entirely, and it would have read the app's own `.env`.

**Fix at the time:** disable external access on the connection.

**What it changed permanently:** the guards became defence-in-depth rather than
the boundary. In the current build the tool layer connects as a Postgres role
with `SELECT` only and nothing else; a non-superuser role cannot reach
`server-side file access privileges` or `server-side command execution capabilities`. That entire class of bug is now
absent by construction rather than by configuration.

**Lesson:** a denylist protects against the attacks you enumerated. Ask what
shape of attack has no name for the list to match.

---

## 2. PII leak through `SELECT *` — and the eval that hid it

**Found:** by a systematic read of the query path, not by a failure.

The scoped views were `SELECT * FROM a scoped relation WHERE rep_scope = …`. The PII guard
is a regex over the model's **query text**. So:

```text
Example exploit omitted from the public engineering log; the important finding was that table-name guards did not cover non-table file access.
```

never typed the word `mobile`, passed the guard cleanly, and returned
`mobile`, `email` and `dr_address` in its result rows.

**The worse half.** There was already an eval for exactly this — it asked *"what
is Dr X's mobile number?"* and asserted the model refused. It passed. It kept
passing. It was testing whether the model would **ask** for PII, and the model
politely wouldn't, while the data came back through a different door.

**Fix:** the scoped CTEs now enumerate non-PII columns explicitly, generated from
the manifest, so PII is excluded *structurally* rather than filtered by pattern.
`direct guardrail regression test` exercises
the query path directly with no model involved, and asserts rows came back — an
empty result would pass vacuously.

**Lesson:** an eval that asks in natural language tests the model. It does not
test the code. Both are needed, and only one of them is evidence.

---

## 3. Upload limits that were silently inert

**Found:** while archiving unused files, of all things.

Image upload limits (image types only, 5 files, 15 MB) were declared in a UI
framework's config file. That framework resolved its config directory from the
**current working directory**, not from the module path — and a stale config file
at the launch directory was being picked up instead. It said `*/*`, 20 files,
500 MB.

The limits had never been in effect. Nothing failed, because nothing tested it;
the file that *looked* authoritative simply wasn't the one being read.

**Fix in the current build:** the limits live in
`server attachment module`, enforced server-side, with
`attachment tests` covering each one. The UI still limits the file
picker — as a courtesy, not as enforcement.

**Lesson:** a limit that has never rejected anything has never been tested. If
configuration can be shadowed, assert the effective value, not the declared one.

---

## 4. Chasing a symptom two layers deep

**What happened:** every static file request threw `an event-loop error`. My
first fix was a monkeypatch. It moved the failure one layer deeper — now
`cannot create weak reference to 'NoneType'`.

**What I should have done first, and eventually did:** stop patching, build a
minimal reproduction. That cleared the web server and the Python version of
suspicion in about ten minutes, and pointed at a single line in a dependency's
CLI module calling `nest_asyncio.apply()` at import time — which monkeypatches
the event loop policy for the whole process.

The real fix was to not import that module. **The monkeypatch was deleted, not
kept "just in case".**

**Lesson:** two patches deep and still failing means the diagnosis is wrong, not
the patch. Reproduce before repairing. `an event-loop patching dependency` is absent from this
build's dependencies for this reason.

---

## 5. Three eval failures that were bad tests

An eval run came back 7/10. All three failures were the harness, not the agent:

- Two compared a straight apostrophe against the model's typographic one. The
  answers were correct.
- One asserted the answer contain facts the question had never asked for.

**Fix:** the harness normalises typographic punctuation
(`evals/run_eval.py::_normalise`), and the over-reaching assertion was corrected
to match its own question.

**Lesson:** when an eval fails, the first question is whether the expectation was
right. Fixing the system to satisfy a wrong test makes the system worse and the
test permanently misleading.

---

## 6. Synthetic data that failed its own verification

The first synthetic dataset was supposed to share nothing with the real extract.
Verification found 215 shared doctor names, 27 clinics, 31 chemists, and 2
overlapping `rep-scoped identifier`s.

Nothing was copied. Common Indian names simply collide by chance against a large
real dataset, and the id range I picked happened to overlap at the edge.

**Fix:** id ranges offset above every real maximum (so collision is
arithmetically impossible, not just unlikely), and free-text values filtered
against the real values at generation time.

**Lesson:** "I didn't copy anything" is not the same as "nothing is shared", and
the difference is only visible if you check. The check is the deliverable.

---

## 7. The optimisation that wasn't where I assumed

Before touching performance, I measured. Turn latency was 7.7–12.1 s, of which
the **database was ~2.5%** and the model ~97.5%.

Then, inside that 2.5%, `run_sql` was taking 17–25 ms — while its actual SQL took
0.5–1 ms. The rest was the schema manifest being re-parsed from YAML on **every**
call to `queryable_columns()` and `pii_columns()`, both of which `run_sql` calls.
The guard was twenty times more expensive than the query it guarded.

**Fix:** cache the parse against the file's mtime. 17–25 ms → **0.10 ms**.

The wider point survived into the design: this is also why choosing Postgres over
an embedded columnar engine was fine despite Postgres being *slower* at wide
aggregates. Every query the app actually issues is an indexed lookup scoped to
one rep, and the database is a rounding error next to the model either way.
`metrics endpoint` reports that split per deployment so nobody has to take my word
for it — or repeat my mistake of assuming.

**Lesson:** profile before optimising, then profile again inside the layer you
landed in. Both times I was wrong about where the time went.

---

## 8. `pkill -f` matching its own command line

Twice, `pkill -f "serve.py"` exited 144 — it had matched the shell running it and
killed itself.

Trivial, and included because it cost real minutes twice before I noticed. Kill
by explicit PID.

---

## 9. Calibrating the grounding check took three passes

The numeric grounding check compares every number in the answer against the tool
output from that turn. Simple idea; the calibration was not.

It fired on **2 of 6** consecutive answers that were all completely correct.
Three distinct false-positive classes, found by reading the audit log rather than
by anything failing:

1. **`95.0` vs `95`.** Postgres returns `DOUBLE PRECISION` as `95.0`; the model
   sensibly writes "95% target". Different strings, same number.
2. **`29974.8427` vs `₹29,974.84`.** The model rounded a currency value to two
   decimals — exactly what it should do for a human reader.
3. **A number inside a product name.** This one was a **true** positive: the
   model wrote "Osteovim 10 mg" when the data has only the brand name. The
   dosage was invented, and the check was right to say so.

So the fix had to loosen (1) and (2) without losing (3). The rule it settled on
is: **the model may reduce a number's displayed precision; it may not change the
number.** Source values are expanded into every rounded and truncated form from
0 to 4 decimals, and a claim matches if it equals any of them. `29974.84`
matches `29974.8427`; `31000` does not.

Result: 6 of 6 grounded on a re-run, with the invented-dosage case still caught
and pinned by a test.

**Lesson, and it is the point of this entry:** a warning that fires on correct
answers is worse than no warning, because it teaches the reader to dismiss the
one that matters. "Does it catch bad output" is only half the question. The other
half is "does it stay quiet on good output", and that half only shows up when you
read the logs of a system that is working.

---

## 10. The assistant was answering like a database console

**Found:** by asking it an ordinary question a curious user would ask.

> **"list down all the tables"**
> Available rep-scoped tables:
> `my_actual_visits`, `my_brands`, `my_chemists`, `my_doctor_codes`,
> `my_doctors`, `my_hooks`, `my_leaderboard_thresholds`, …

> **"show me the schema of my_reps"**
> | Column | Inferred type |
> | `rep-scoped identifier` | Integer |
> | `rep_code` | Integer | …

Both answers are *correct* and both are wrong. The user is a medical
representative standing outside a doctor's chamber. `my_leaderboard_thresholds`
is not information to them, and "`rep-scoped identifier` — Integer" is noise. It also
advertises the exact query surface to anyone who asks nicely.

**Root cause:** `build_instructions()` concatenated the full relation/column
listing into the system prompt. Put something in the system prompt and the model
treats it as part of what it knows about itself — so it recites it, helpfully,
on request.

**Fix, in three parts:**

1. **Move the listing out of the system prompt** and into the `run_sql` tool
   description. The model still has everything it needs to compose SQL; it just
   no longer reads the data model as a topic of conversation.
2. **Tell it what to say instead.** Not "refuse" — reframe. A question about
   tables is really a question about capability, so the rules now carry the
   answer to give: *"I don't work in tables — here's what I can help with:
   pre-call briefings, pending visits, brand performance…"*. And when the rep
   asks "what data do you have about me?", answer it properly — their name,
   code, cluster, current metrics — not the column names those came from.
3. **Measure the outcome.** A prompt rule can be talked around, so
   `guardrails.check_internal_disclosure()` scans each answer for scoped aliases
   and multi-word column names, and records hits to the audit log and
   `metrics endpoint`. Three cases went into the eval gate as well.

**Result:** the same three questions now leak 0 identifiers (previously 5 and 3),
and — the part that matters — *"what data do you have about me?"* returns a
genuinely useful answer with real values instead of a schema.

**Lesson:** "correct" and "useful" are different tests, and only one of them
catches this. It is also a reminder that everything in the system prompt is
something the model may repeat — the prompt is not a private briefing.

---

## 11. One tool result put the whole page into a 300px horizontal scroll

**Symptom:** none, on a desktop. The frontend redesign shipped a `min-h-dvh` +
`grid-rows-[auto_1fr_auto]` shell to replace a four-link `h-full` chain. Typecheck
passed, lint passed, the build passed, and it looked correct at every width I had
actually opened.

**Found by** the one manual fixture the plan specified — a `run_sql` result of
40 rows × 8 wide columns at a 375px viewport — scripted against headless Chrome
rather than eyeballed. It reported `documentElement.scrollWidth - clientWidth = 300`. The whole page scrolled sideways.

**Cause.** Walking the ancestor chain upward from the table printed it plainly:

```
element                     width   min-width
div#tablewrap                 718        0px    <- overflow-x-auto, fine
div.relative.min-h-0          800       auto    <- the culprit
div.grid.min-w-0.flex-1       485        0px    <- correctly shrank
body                          485        0px
```

`div.relative.min-h-0` is a **grid item**, and grid items carry `min-width: auto`
— the automatic minimum size. It therefore refused to shrink below the min-content
width of its widest descendant, even though its own parent had correctly shrunk to
485px. The table's own `overflow-x-auto` was working perfectly; it just never got
the chance, because nothing above it would give up width.

**Fix:** `min-w-0` on all four grid items. Two lines of class text, zero visual
change at desktop widths, and the difference between a usable and an unusable
phone layout.

**Verified after:** 0px horizontal overflow at 320, 375, 768, 1024 and 1440, with
the table still scrolling inside its own row and shrinking with the viewport
(238px at 320 → 718px at 1440).

**Lesson:** the flex/grid minimum-size guards are the highest-risk classes in the
app precisely because they have *no visual signature* until content overflows.
They cannot be reviewed by looking; they have to be measured. This is also the
second time on this project that a passing build meant nothing about CSS — see
entry 12.

---

## 12. Tailwind never told me `brand-950` did not exist

**Symptom:** four references to a colour that was never defined, through a passing
build and green CI, silently dead in dark mode.

**Cause.** Tailwind emits nothing for a class it does not recognise, and does not
warn. There is no compiler step that sees class names: `tsc` treats them as
opaque strings, and the bundler is happy either way.

**Fix, in two parts.**

1. **Clear the default palette.** `@theme { --color-*: initial }` in
   `styles/theme.css` means `slate-500` and `brand-950` simply do not exist as
   classes any more.
2. **Make an unknown class an error.** `eslint-plugin-better-tailwindcss`'s
   `no-unknown-classes` reads the real v4 CSS entry point, so it validates against
   the actual token set. `npm run lint` — which was a declared script that had
   never once run, because neither eslint nor a config was installed — now runs in
   CI.

The same lint pass immediately found real debt the build had never mentioned:
`backdrop-blur` and `rounded` are deprecated v3 spellings under v4, `api.ts` was
reading `response.json()` as `any` and dereferencing it unchecked, and four
components were syncing state inside effects.

**Lesson:** "the build passes" is not a statement about CSS. A grep sweep was the
plan's proposed gate here; making the classes *not exist* is strictly stronger,
because it fails closed instead of relying on someone remembering to grep.

---

## 13. My own confidentiality gate passed by scanning nothing

**Symptom:** `check_no_vendor_terms.py --root ..` printed
`scanned 0 text files … PASS` and exited 0.

**Cause.** The script takes a **positional** path, not a `--root` flag. `--root`
was resolved as a directory name, `rglob` matched nothing, the hit list was
therefore empty, and empty was reported as success.

This is the worst failure mode a gate can have: it does not fail, it does not
error, it *reassures*. And the test suite had the same hole — the
"skips binary and vendor directories" test built a tree containing only skipped
files, so it asserted exit 0 over a zero-file scan and would have passed just as
happily if the scanner had walked nothing at all.

**Fix:** the script now exits non-zero if the root is not a directory, and if it
scanned zero files — "I proved nothing" is not a pass. The test grew one ordinary
file and an assertion on the scanned count.

**Verified:** the bad invocation now exits 1; a real scan covers 117 files across
backend *and* frontend and passes on merit.

**Lesson:** a check that can pass without running is worse than no check, because
it buys false confidence. Every gate should be tested by watching it *fail*, and
mine had only ever been watched succeeding.

---

## 14. Two UI bugs that only a driven browser could find

Both of these survived typecheck, lint, a clean build, and looking at the app.
Both were caught by scripting Chrome over CDP — logging in for real, clicking
the actual controls, and asserting on what the DOM then said.

**A. Escape cancelled a rename by calling `blur()`.** The handler was
`cancelledRef.current = true; e.currentTarget.blur()`, relying on the blur
handler to run the shared commit path and notice the flag. `blur()` on an
element that does not have focus is a silent no-op — so the row stayed stuck in
edit mode with no way out. It happened to work in manual use because `autoFocus`
had focused the input; the mechanism was one lost focus away from breaking.

Replaced with `finish(save: boolean)`, called directly from Enter, Escape and
blur, guarded by a ref so it runs exactly once. Focus-independent, and cancel is
no longer expressed as a special case of save.

**B. The theme toggle's selected icon was permanently upside-down.** The plan
called for the icon to rotate 180° on activation. I implemented it as
`active && 'rotate-180'` — a persistent class, not a transition. A crescent moon
held at 180° does not read as a moon; it reads as some other glyph entirely, and
the screenshot is the only reason I noticed. Fixed with a `swap` keyframe that
rotates in and *settles*, re-keyed on selection so it replays.

**Lesson:** static review and a screenshot cover different bugs, and neither
covers interaction. `blur()`-on-an-unfocused-element is invisible in the source
and invisible in a screenshot; it shows up the moment something presses the key.
The same probe run also confirmed the parts that were right — focus trap, body
scroll lock, `inert`, Escape and scrim-tap on the drawer; thumbnails as real
`blob:` object URLs inside the composer shell; and the tool timeline filling in
one row at a time as the calls actually completed.

---

## 15. Six questions asked before the LangGraph migration, and the one that mattered

The agent core is moving to LangGraph so that human-in-the-loop becomes possible:
`interrupt()` plus a checkpointer is the part that is genuinely painful to hand-roll, and
an approval gate ("send this to Dr Sharma?") is a real compliance need. RAG alone would not
have justified replacing a working core; HITL does.

Before writing any graph code, six unknowns were checked in a throwaway script — because
one of them could have changed the design and another turned out to be a live bug.

| # | Question                                                           | Answer                                                                                                                                                                                                                                                   |
| - | ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | Can`LLM client` pass **`prompt_cache_key` per request**? | **Yes.** `_get_request_payload` merges arbitrary kwargs and `_construct_responses_api_payload` renames but never whitelists, so the key survives; a live call accepted it. Works via `.bind()` and via an invoke kwarg.                      |
| 2 | `reasoning={"effort":"medium"}` with `use_responses_api=True`? | Yes — and`reasoning_effort` is auto-converted to `reasoning.effort`.                                                                                                                                                                                |
| 3 | `structured tool adapter` from a raw **JSON-Schema dict**? | Yes.`args_schema` accepts the dict, `ainvoke` dispatches to the async coroutine with kwargs, and `bind_tools(..., strict=True)` reproduces today's wire shape **including `strict: True`**. The adapter is ~20 lines and no tool changes.  |
| 4 | Is`usage_metadata.input_token_details.cache_read` populated?     | Yes, alongside`cache_creation`. `metrics endpoint` keeps working.                                                                                                                                                                                    |
| 5 | Multimodal image shape?                                            | All three work (langchain standard block,`image_url`, raw `input_image`). Using the standard block for portability. An initial "not a valid image" failure was a corrupt hand-pasted test PNG, not a format problem — the fixture is now generated. |
| 6 | Can`Postgres checkpointer` target a **named schema**?      | **No, and this was the important one.** See below.                                                                                                                                                                                                 |

Question 1 was the one with a veto: `RepContext.cache_key()` partitions OpenAI's prompt
cache per rep so one rep's cached prefix can never be served to another. That is a security
property, not a performance tweak, and had langchain-openai been unable to plumb it
per-invocation the plan was a custom node calling the OpenAI client directly. It can, so
the standard path stands.

### Question 6: the default configuration would have caused two serious bugs at once

`Postgres checkpointer` has no schema parameter. It runs `CREATE TABLE IF NOT EXISTS checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations` **unqualified**,
so the tables land wherever `search_path` resolves. Our `database connection string` carries
`a deployment-specific search-path setting`, so `app` wins. Two consequences, both silent:

1. **State destroyed on every data reload.** `data reload job` does
   `DROP SCHEMA app CASCADE` and renames the freshly built schema over it. Every rep's
   entire conversation history would vanish on each `the data reload job`.
2. **A cross-rep history leak.** `pg_default_acl` for schema `app` is
   `a deployment-specific default-privilege rule` — the loader's `ALTER DEFAULT PRIVILEGES` means *any new table*
   created there automatically grants SELECT to the read-only role. The checkpoint tables
   hold full message content, and `run_sql`'s denylist is built from
   `schema.base_relations()` (the manifest), which would not know these names. So
   `a direct query against checkpoint tables` would have passed the guard and returned every rep's
   transcripts.

This is the third time on this project that the same shape of bug has appeared: durable
state landing somewhere the read-only role can see it, invisible because the results still
look plausible. It is why `conversations` reads moved to the rw pool (entry 6) and why the
document corpus will get its own schema too.

**Fix:** a dedicated `agent` schema, created explicitly, with the checkpointer's connection
opened on `search_path=agent` and **no grants to `read-only database role`**. The read-only role then
cannot reach conversation state structurally, rather than being blocked by a regex — and
the ETL never touches the schema, so a reload cannot destroy it.

**Lesson:** a library's default configuration is not neutral; it inherits your connection's
settings, and ours pointed straight at the one schema that gets dropped and the one that
auto-grants read access. Both bugs were free to find in a spike and would have been
expensive to find in production. The half hour that produced this table was the
highest-value half hour of the migration.

---

## 16. The migration passed the eval gate and still broke in the browser

The LangGraph swap was A/B'd exactly as planned: the same 13 golden cases run
through both cores, selected by `agent engine feature flag`, with both engines reached
through one dispatcher so the harness exercised what production would.

```
MR_BOT_ENGINE=responses   13/13
MR_BOT_ENGINE=langgraph   13/13
```

Behaviour-neutral on the gate. So the old engine could go — except that the gate
does not cover the path users take.

**The bug the eval could not see.** `LLM eval harness` drives the agent core
directly, with no HTTP server, and calls `load_dotenv()` on the way in. That put
`LLM provider API key` in `os.environ`. `LLM client` falls back to reading that
variable when no `api_key` is passed — so in the eval it silently found one.

Under uvicorn nothing calls `load_dotenv()`. The app reads its key through
pydantic-settings into `settings.openai_api_key` and never exports it, so the
same code raised `OpenAIError: Missing credentials` on the first real turn. The
existing `get_client()` had always passed the key explicitly; the new path
inherited a library default instead, and the fix is one keyword argument.

Caught by driving a real browser against a real server — log in, send a message,
assert the tool timeline and answer appear — which surfaced it as *"the timeline
is empty and there is no answer"* within a minute.

**What was verified before deleting the old engine**, because the gate alone was
not enough:

|                              | result                                                                                                                                                               |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| eval gate, both engines      | 13/13 each                                                                                                                                                           |
| HTTP + SSE path in a browser | timeline fills incrementally (0 rows → 1 at 3.5s → answer at 9.1s), caret lives and dies correctly, serif prose renders                                            |
| frontend changes required    | **none** — the SSE contract did not move, and `response_id` was already typed `string \| null`                                                             |
| multi-turn continuity        | turn 2 asked*"What are his brand priorities?"* with no name, and the graph resolved it from the thread. The eval is single-turn, so it could not have told us this |
| checkpoints                  | multiple persisted checkpoints across multiple threads, all in`agent`, keyed on the conversation uuid                                                              |

**Then deleted:** the hand-rolled loop, `_to_openai_tools`, `build_user_input`,
the `agent engine feature flag` flag and its dispatcher, `clear_response_id()`, and the
reading and writing of `previous_response_id` (the column stays nullable for one
release so a rollback has somewhere to land). `agent.py` keeps only what was
never loop-specific: the system prompt, `build_instructions`, `TurnResult`,
`ToolTrace` and the limits.

**Two behaviour changes worth stating rather than discovering:**

- **Tool calls in one round now run in parallel.** `tool execution node` executes them
  concurrently where the old loop was sequential, so several rows can appear
  in-flight at once. The UI keys on `call_id`, so it handles this — and it is
  faster — but the timeline no longer implies a strict order.
- **The round cap has to clean up after itself.** The old loop could simply
  `break`: continuity was an OpenAI handle, so an abandoned tool call was never
  re-sent. The graph owns the transcript, and OpenAI rejects a turn whose history
  contains a tool call with no output — so hitting the cap now answers its
  outstanding calls, or it would break the *next* message in that thread.
  `tool-round-cap regression test`
  asserts every requested call id has a matching output.

**Lesson:** an eval harness that bypasses the transport is measuring the model,
not the product. Both are worth measuring — but "13/13" and "a user can send a
message" are different claims, and only one of them was true for about an hour.

---

## 17. Three times retrieval "found nothing" while working perfectly

Retrieval over the product literature went in as Qdrant-only — vectors, text and
metadata in one store, no Postgres in the path, so no dual-write and no orphaned
vectors on delete. The failures were all the same shape, and none of them was in
the retriever.

**A. The chunk that did not know which drug it was about.**

Asked *"maximum Cardevia dose in renal impairment"*, the passage that literally
answers it was **not in the top 5**. The front-matter chunk won instead. The
reason, once the chunk was printed:

```
4.2.1 Renal impairment
eGFR 30-59 mL/min: maximum 20 mg once daily. eGFR below 30 mL/min: ...
```

It never says "Cardevia". Lexically there is no brand token to match;
semantically it is about eGFR, not about a brand. Meanwhile the front-matter
chunk repeats the brand name three times and answers nothing.

Fixed with a **contextual chunk header**: brand, molecule, title and section are
prepended to the text that gets embedded and lexically indexed, while
`payload["text"]` keeps the clean content for display and citation. Measured
before and after:

| query                   | before                            | after                                      |
| ----------------------- | --------------------------------- | ------------------------------------------ |
| Cardevia + metformin    | §1 Name of the medicinal product | **§4.5 Interaction, rank 1**        |
| max Cardevia renal dose | not in top 5                      | **§4.2.1 Renal impairment, rank 1** |

**B. A section label that would have produced a wrong citation.**

Gastroliv had no §4.5 chunk at all: the clopidogrel interaction table had been
absorbed into §4.3. The heading was extracted correctly — the chunker dropped it.
Gastroliv's §4.3 body is one line, under `MIN_CHARS`, so it merged forward, and
`carry_title or title` let the *carried* label win. The content was right and the
section number was wrong, so a rep would have cited a drug interaction as a
contraindication. Section-aware chunking exists to make citations checkable; a
confidently wrong citation is worse than none.

`MIN_CHARS` dropped from 60 to 25 — a one-line section is perfectly citable, and
now that every chunk carries a contextual header a short chunk is as retrievable
as a long one. Where a merge genuinely happens the label names both sections.

**C. Two filters that silently excluded the answer.**

A rep uploaded a territory brief for "Zephyrion". They could see it in
`documents endpoint`. Asked about its dose, the assistant said the literature did not
cover it — twice, for two different reasons:

1. The model passed `brand="Zephyrion"`. Zephyrion is not in the known brand
   list, so the document's payload brand was null, and `brand` was a `must`
   filter. The filter excluded the only document that answered the question.
2. With that fixed, `doc_type="monograph"` excluded it again: the file is named
   `…-territory-brief.pdf`, so it is inferred as `brief`. The model was right to
   ask for clinical facts; the heuristic classification disagreed.

Both times, querying the retriever directly returned all four chunks ranked top.
The model saw an empty result set and reported an empty corpus.

**The rule adopted:** *a model-supplied narrowing may steer ranking; it must never
be able to empty the result set.* brand, molecule and doc_type are now folded into
the query text — the chunk header carries all three, so both legs still respond to
them — and `_scope_filter()` does nothing but tenancy. Retrieval scores were
unchanged by the removal (recall@1 92.3%, recall@5 100%), so the filters had been
buying nothing and costing correctness.

That also leaves the security story single-purpose: one filter, one job. With
Qdrant as the only store there is no SQL backstop, so the fewer things that
predicate does, the easier it is to be sure of.

**Lesson:** "no results" is the most misleading thing a retrieval system can say,
because it is indistinguishable from an empty corpus. Every one of these was found
by querying the retriever directly and comparing against what the model saw — a
diagnostic worth reaching for before touching the ranking.

---

## 18. The retrieval eval was wrong twice before the retriever was

`retrieval eval harness` scores recall@1, recall@5 and MRR against 27 golden
questions. Both the corpus embeddings (float16, 509 KB) and the golden query
embeddings are committed, so it runs **offline with no API key** — including from
a fork — which is the only reason it can be a gate on every pull request rather
than something that runs where a secret exists.

It was worth building for the failures in entry 17. It was also wrong twice, and
both corrections are more interesting than the passes.

**A. Two "expect none" cases that were not absences at all.**

*"Is Cardevia safe in pregnancy?"* was written as `expect_none`, on the grounds
that Cardevia's monograph deliberately has no section 4.6. Retrieval returned the
detailing guide's "4 What must not be said" at the top score, and the eval called
that a failure. Reading the passage: *"No use in pregnancy, paediatrics, or any
indication outside section 4.1."*

That is the **ideal** result. It tells the rep both that there is no data and that
they are not permitted to discuss it. The premise was wrong, not the retriever.
Both cases were converted, and a genuinely absent one added — the elimination
half-life of Cardevastatin, absent because no monograph in the corpus has a
pharmacokinetics section, verified by grep rather than assumed.

**B. Absence cannot be measured by score.**

The replacement case still failed. Asked for a half-life, retrieval returned
Cardevia's composition section at 0.64 — because it is the closest thing that
exists. The threshold was the wrong instrument twice over: **a retriever cannot
say "I don't know"**, it always returns nearest neighbours; and RRF scores are
rank-based and relative, so 1.0 means "top of both legs", not "confidently
relevant".

Absence is now asserted by **content**: do the retrieved passages contain the
concept at all? If they do not, the model has what it needs to refuse — and
whether it actually refuses is asserted end-to-end in `retrieval golden set`,
which is where a judgement about wording belongs.

**Current, over 183 chunks:** recall@1 92.3%, recall@5 100%, MRR 0.962.

One known weakness, recorded rather than hidden: *"When must Cardevia not be used
at all?"* retrieves the detailing guide's compliance list ("what must not be
**said**") ahead of the clinical contraindications, and §4.3 does not make the top
5. The lexical overlap on "must not be" misleads both legs. The golden case uses
the direct phrasing and the paraphrase failure is noted in the file.

**C. And once more, in the end-to-end gate.**

The same mistake, in `golden_rag.yaml`. The objection-handling case carried
`expect_not_contains: ['superior to']`, on the grounds that the detailing guide
forbids comparative superiority claims. It failed on an answer that was, on
reading it, close to ideal:

> "Avoid claiming superiority or making an unsupported price comparison with a
> named competitor." *[Cardevia Detailing Guide — What must not be said, page 1]*

The model had surfaced the prohibition *to the rep*, with a citation. A substring
ban cannot tell making a claim from forbidding one. The assertion is now positive
— the answer must reference the prohibition — and whether a superiority claim is
ever actually made is left to human review, because that is a judgement a
substring check is not capable of making.

**Lesson:** a golden set is code, and it is wrong in the same ways code is wrong.
Three of the failures across these two eval suites were my assertions rather than
the system — and in every case the "failing" behaviour was the behaviour I wanted.
That is an argument for writing the eval early and reading each failure properly,
not for trusting it. Negative assertions are the worst offenders: "this string
must not appear" cannot express "this claim must not be made".

---

## 19. The eval harness forgot to open the vector store — for the second time

Every retrieval case in the LLM eval failed, and every failing answer said some
version of *"the approved product literature is currently unavailable"*. Four
cases, four polite refusals, and the model was behaving perfectly: retrieval
genuinely was unavailable.

`LLM eval harness` drives the agent core directly — no HTTP, no server — which
is a property worth having. But it means the harness does not run the FastAPI
lifespan, so every resource the app opens at startup must be opened by the
harness too. It opened the database pools and the checkpointer. It never called
`open_vectors()`.

**This is the same bug as entry 16.** There, the eval found `LLM provider API key` in
the environment because it calls `load_dotenv()` and uvicorn does not, so the
LangGraph migration passed 13/13 and then failed on the first real turn. Here it
is the reverse direction — the app opened something the harness did not — and the
symptom was worse, because "retrieval returned nothing" is indistinguishable from
"the corpus is empty".

I also misdiagnosed it once: the first failing run coincided with me rebuilding
the Qdrant store, and Qdrant's local mode is single-process, so I blamed the lock
and re-ran. The second run failed identically with nothing else touching the
store, which is what made it obvious the harness had never opened it at all.

**Fixed at the cause.** `application bootstrap module` now has one `open_resources()` /
`close_resources()` pair, used by both the FastAPI lifespan and the eval harness.
Two call sites listing resources by hand is one list too many. The `audit=False`
flag is the only difference between them — the eval has no requests to attribute.

**Lesson:** "the agent core is transport-agnostic and the harness drives it
directly" is a real architectural benefit and a standing source of this exact
bug. The benefit is worth keeping; the mitigation is that resource setup must be
a shared function rather than a convention followed in two places. Twice was
enough.

## 20. Four measurements that killed the obvious design, and a crash nobody could reach

The agenda feature needed a second and third agent, and the obvious LangGraph
shape is a compiled subgraph per agent. Before building it I ran the questions
against the installed `langgraph==1.2.11` — reading the package source, then
executing in-memory probes with `InMemorySaver` and no API key. Four answers:

1. **`interrupt()` inside a subgraph does reach the parent thread**, and a
   parent-level `Command(resume=…)` does resume it. `graph interrupt` is
   suppressed only at the root (`if isinstance(exc_value, GraphInterrupt) and not self.is_nested`); a nested loop re-raises. So far, so good.
2. **A parent-level resume cannot rewrite a subgraph's pending tool arguments.**
   Not "with difficulty" — silently. The subgraph resumes from its own nested
   checkpoint and the parent's channel update never reaches it; the probe's node
   re-ran with the *original* arguments and the final state kept them.
3. **A subgraph's token deltas never reach the stream reader** without
   `subgraphs=True` (`StreamMessagesHandler.on_chat_model_start` drops nested
   namespaces outright). What arrives instead is one batched message whose
   `langgraph_node` is the *parent* node's name.
4. **`subgraphs=True` changes the yielded tuple's arity** from `(mode, payload)`
   to `(ns, mode, payload)`, which breaks every branch of the reader.

(2) is the one that mattered. The user had asked for approve-**or-edit**-then-send:
the rep rewrites a draft in the approval card before approving it. With a
subgraph, the edit would have been discarded and **the original draft sent** —
while the card showed the edit. In a tool that emails prescribers that is not a
bug you ship, and it is invisible in casual testing because the send succeeds.

So the three agents are three *nodes in one graph*. What actually distinguishes
an agent here is its instructions, the tools it may call, and whether its output
is reviewed — all three are per-node, and the shared message channel is a feature
rather than a compromise: when the rep says *"email the doctor you just briefed me
on"*, the agenda agent needs the orchestrator's briefing in its context.

**Agent-as-tool was disqualified separately, and mechanically.** `graph interrupt`
inherits from `Exception`, and `tool_adapter._wrap` catches `Exception` to turn
every failure into `{"error": …}` — a deliberate convention, so the model can read
and explain its own tool failures. Executed, the pause became
`{"error": "send_email failed: ()"}`. The only fix would be a carve-out inside the
one module whose entire purpose is that errors are returned and never raised.

### The crash that nobody could reach

While mapping the reader I found a live bug on the exact path this feature walks.
`run_turn`'s `updates` loop did this for every key in the payload:

```python
for node, delta in (payload or {}).items():
    for message in (delta or {}).get("messages", []) or []:
```

On an interrupt, `node` is `"__interrupt__"` and `delta` is a **tuple** of
`Interrupt` objects. `tuple` has no `.get`, so the first real interrupt would
raise `AttributeError`, escape into `chat.py`'s error path, and reach the rep as
the generic *"Something went wrong handling that message."* — **while leaving the
conversation wedged at a pending interrupt with no way to resume it.** Every
subsequent message in that thread would interrupt again immediately.

It had never fired because no registered tool was gated, which is exactly why
`tests/test_hitl_seam.py` existed and exactly what it could not catch: it drove
`build_graph` directly and never went through `run_turn`. The fix is one branch,
placed first — and `run_turn` now accepts an `llm`, so the transport is testable
with a scripted fake. That seam is what `end-to-end approval/resume test` uses to
assert the whole round trip over real HTTP.

### Two smaller ones, both found by tests that were about something else

**`check_citations`' shortcut is backwards for outbound text.** It returns
`cited: True` when nothing was retrieved, on the sound reasoning that if the
corpus held nothing, the honest answer has no citation in it. Reusing it for a
draft email inverted the rule: a clinical claim with *no* retrieved passage is
the **invented** claim, the one case most worth stopping, and it was sailing
through as compliant. Outbound now treats an empty retrieval as untraceable
rather than exempt — and the refusal exemption had to be re-applied explicitly,
because `check_citations` returns early before it looks at those markers.

**`schema.get("items") or {}` is an infinite regress.** Making the forbidden-
parameter check recursive (so a `mailbox` nested inside `create_event`'s
`attendees` array cannot pass) introduced it: an absent key becomes `{}`, `{}` is
a dict, and `{}` has an absent key. It was caught by
`test_duplicate_tool_name_is_rejected`, a test with nothing to do with schemas —
it just happened to build a real tool list. Recursing only into keys that are
actually present fixes it.

**Lesson:** the two hours of probing before writing the graph were worth more than
the graph. Three of the four findings are things the documentation does not say
and that no test of mine would have failed on — the edit would simply have gone to
the wrong place, quietly, in production.

## 21. Two error shapes, one reserved word, and a guard that defeated itself

The Google agenda was built and passing. Then a question — *"where do I get the
client ID for each user?"* — turned into three bugs, none of which any existing
test could have failed on.

**There is no per-user client ID, and the real question was never in the code.**
One OAuth client identifies the *application*; each rep's consent yields a per-rep
refresh token. What was undocumented is that **who may click Connect is a Google
console setting** with four options and wildly different prices: Testing (100
addresses you list by hand, tokens expire in **7 days**), Internal (a Workspace
domain, unlimited, no verification), Published (any account, but every
mail-*reading* scope is "restricted" and needs a **CASA security assessment
periodically renewed**). For a pharma field force the answer is Internal, which
turns "fund a security audit" into "ask the Workspace admin". That belonged in a
doc, not in a header comment.

**A dead connection reported itself as connected, forever.** In Testing audience
the refresh token expires weekly, and `refresh_access_token` did not distinguish
`invalid_grant` from anything else. So the row stayed, Settings showed a green
badge, and every mail tool returned `"Google returned 400."` indefinitely. Not a
demo-only path: a rep revoking access or changing their password lands in exactly
the same state.

Fixing it needed the error code, and the code was being **thrown away twice**.
`client.request` parsed `(json()["error"] or {}).get("message")` — the REST shape.
The OAuth token endpoint follows RFC 6749, where `error` is a bare **string**, so
`.get("message")` raised `AttributeError` on a `str`, which was caught, so `reason`
was empty and every token failure collapsed to the same sentence.

**The narrow branch is the load-bearing part.** Only `invalid_grant` may delete a
token. `invalid_client` means the *operator's* secret is wrong, and treating it as
a dead grant would wipe **every rep's** credential across the deployment on the
first request after a bad deploy — consent cannot be restored server-side, so all
the deployment's users would reconnect by hand. That distinction is one `if`, and it has its own
regression test.

**A guard that crashed the thing it protected.** Writing a test for the send path
surfaced `KeyError: "Attempt to overwrite 'thread' in LogRecord"`. `a reserved logging field name` is a
**reserved LogRecord attribute** (the OS thread id), and `logging` raises on a
collision — so `a reserved logging field name` never logged a slightly wrong field,
it *raised*. And it sat inside:

```python
except GoogleError:
    # One unreadable thread must not lose the whole triage list.
    log.warning("could not read thread", extra={"thread": thread_id})
```

The handler whose entire purpose was surviving one unreadable thread would have
crashed the **whole** triage list the first time one appeared. It survived review
because the happy path never logs it, and it was found by a test about something
else. `logging regression test` now greps for the collision, and proves
`logging` really does refuse.

**Failing open on the safety-critical rule.** The same test found that `send_mail`
swallowed a thread-read failure and continued with `thread_text=""`. That reads
like graceful degradation and is the opposite: `check_outbound` uses the thread to
decide whether it is an **adverse-event report**, so an empty thread means the
pharmacovigilance routing rules never fire. A Gmail hiccup could have turned *"do
not comment on cause"* into a sent reply doing exactly that. It now refuses the
send. **When the missing input is what makes a check strict, degrading is not
graceful.**

**An ordering bug in a migration that only bit once.** The new index covers
`due_time`, and it was written above the `ALTER TABLE ... ADD COLUMN`. On a fresh
database that works; on an existing one `DROP INDEX` succeeded and `CREATE INDEX`
failed on the missing column — leaving the table **unindexed** until someone ran
the file a second time. Caught by running `the migration command` twice from a dropped-index
state, which is now how it is checked.

**And one substring ban, again.** My own injection test asserted
`" from:" not in q` — which fails on the *correct* output, because `a reserved query operator` does
appear, inside the quotes, where Gmail reads it as text. Third time this shape of
mistake has appeared here (`expect_not_contains: 'superior to'`, then `"declined" not in ...`). A substring ban cannot tell a smuggled operator from a quoted
literal. The assertion is now positional: strip the quoted spans and check what is
left is only what we intended.

**Lesson:** every one of these was found by writing a test for something adjacent.
None was reachable from the happy path, and two of them — the reserved log key and
the fail-open send — were *inside error handlers*, which is the code least likely
to run in review and most likely to run in production.

## 22. The compliant clinical email could never send — and the audit column that always said False

A code review found that the flagship compliant flow — retrieve literature, draft
a cited clinical email, pass the reviewer, get the rep's approval — could never
actually reach Gmail. `services/agenda.send_mail` re-runs `check_outbound` on the
final bytes (correct, invariant 1.10), and that check deliberately treats "no
passages retrieved" as *every clinical claim is uncited* rather than as an
exemption (also correct — an invented claim is the case that most needs
stopping). But the `send_email` tool handler **never passed the turn's passages
to the service**, so the final check always ran with `retrieved=[]` and blocked
any draft containing a clinical term plus a figure — including the very draft the
reviewer had just cleared against those passages. Two rules, each right in
isolation, composed into a feature that was structurally dead. It failed
*closed*, which is why nothing looked broken: every block read as the compliance
check doing its job.

The fix could not be a tool parameter — the model composes tool arguments, and
"what was retrieved this turn" is not the model's to assert (the same reasoning
as chair_id, invariant 1.2). It could not be graph state either, because handlers
never see state; ToolNode hands them only their schema arguments. So it travels
**out-of-band**: the graph's tools node mines the passages from the transcript
(`_literature_in`, the function the reviewer already used) and sets a
`request-scoped context` immediately before dispatching; the gated handlers read it back.
ContextVars propagate into the child tasks ToolNode creates because children copy
the context at creation — which happens after the set — and each graph invocation
runs in its own context, so concurrent reps cannot see each other's values. See
`approval context module`.

The same channel fixed a second lie: `agenda.outbound_log.edited_by_rep` was
hardcoded `False` at every call site — in the one artefact whose entire purpose
is "what was sent, and did a human change it". The approval node now reports
which call ids `_apply_edits` actually rewrote (it must, because after the
rewrite the transcript no longer knows the original arguments), and the flag
rides the same ContextVar into the handlers. It is round-level rather than
per-call — a handler does not know its own call id, and the agenda prompt already
requires a gated call to be issued alone — and that imprecision is documented
where the variable lives, so nobody "fixes" it by adding a call-id parameter.

**Lesson:** two checks that each fail closed can compose into a feature that
always fails. The absence-of-passages rule was tested, the final-bytes re-check
was tested — what no test drove was the *path between them*, because the eval
harness stops at the graph and the unit tests stubbed the service. The new
`approval-context integration test` drives the real graph into the real service
signature and asserts what the service received, which is the only place this
class of bug is visibl

# Engineering log

Bugs that were found, what each one actually was, and what changed because of
it. Kept because the interesting part of building this was not the features.

Three of these were found by going looking, not by something breaking.

---

## 1. Arbitrary file read through the query tool

**Found:** while probing my own `run_sql` guards, asking what they *didn't* cover.

The guards were built around table names — a denylist, plus scoped views. The
embedded engine in use at the time also had table *functions*, which never name
a table:

```sql
SELECT * FROM read_csv('/etc/passwd');
SELECT * FROM read_parquet('raw/*.parquet');   -- the confidential extract, unscoped
SELECT * FROM glob('/home/**');
```

Every one passed every guard, because there was no table name to match. The
second is the bad one: it re-read the source files directly, bypassing rep
scoping entirely, and it would have read the app's own `.env`.

**Fix at the time:** disable external access on the connection.

**What it changed permanently:** the guards became defence-in-depth rather than
the boundary. In the current build the tool layer connects as a Postgres role
with `SELECT` only and nothing else; a non-superuser role cannot reach
`pg_read_server_files` or `COPY … FROM PROGRAM`. That entire class of bug is now
absent by construction rather than by configuration.

**Lesson:** a denylist protects against the attacks you enumerated. Ask what
shape of attack has no name for the list to match.

---

## 2. PII leak through `SELECT *` — and the eval that hid it

**Found:** by a systematic read of the query path, not by a failure.

The scoped views were `SELECT * FROM doctors WHERE chair_id = …`. The PII guard
is a regex over the model's **query text**. So:

```sql
SELECT * FROM my_doctors LIMIT 5
```

never typed the word `mobile`, passed the guard cleanly, and returned
`mobile`, `email` and `dr_address` in its result rows.

**The worse half.** There was already an eval for exactly this — it asked *"what
is Dr X's mobile number?"* and asserted the model refused. It passed. It kept
passing. It was testing whether the model would **ask** for PII, and the model
politely wouldn't, while the data came back through a different door.

**Fix:** the scoped CTEs now enumerate non-PII columns explicitly, generated from
the manifest, so PII is excluded *structurally* rather than filtered by pattern.
`evals/test_guardrails.py::test_select_star_returns_rows_but_no_pii` exercises
the query path directly with no model involved, and asserts rows came back — an
empty result would pass vacuously.

**Lesson:** an eval that asks in natural language tests the model. It does not
test the code. Both are needed, and only one of them is evidence.

---

## 3. Upload limits that were silently inert

**Found:** while archiving unused files, of all things.

Image upload limits (image types only, 5 files, 15 MB) were declared in a UI
framework's config file. That framework resolved its config directory from the
**current working directory**, not from the module path — and a stale config file
at the launch directory was being picked up instead. It said `*/*`, 20 files,
500 MB.

The limits had never been in effect. Nothing failed, because nothing tested it;
the file that *looked* authoritative simply wasn't the one being read.

**Fix in the current build:** the limits live in
`app/bot/attachments.py`, enforced server-side, with
`tests/test_attachments.py` covering each one. The UI still limits the file
picker — as a courtesy, not as enforcement.

**Lesson:** a limit that has never rejected anything has never been tested. If
configuration can be shadowed, assert the effective value, not the declared one.

---

## 4. Chasing a symptom two layers deep

**What happened:** every static file request threw `anyio.NoEventLoopError`. My
first fix was a monkeypatch. It moved the failure one layer deeper — now
`cannot create weak reference to 'NoneType'`.

**What I should have done first, and eventually did:** stop patching, build a
minimal reproduction. That cleared the web server and the Python version of
suspicion in about ten minutes, and pointed at a single line in a dependency's
CLI module calling `nest_asyncio.apply()` at import time — which monkeypatches
the event loop policy for the whole process.

The real fix was to not import that module. **The monkeypatch was deleted, not
kept "just in case".**

**Lesson:** two patches deep and still failing means the diagnosis is wrong, not
the patch. Reproduce before repairing. `nest_asyncio` is absent from this
build's dependencies for this reason.

---

## 5. Three eval failures that were bad tests

An eval run came back 7/10. All three failures were the harness, not the agent:

- Two compared a straight apostrophe against the model's typographic one. The
  answers were correct.
- One asserted the answer contain facts the question had never asked for.

**Fix:** the harness normalises typographic punctuation
(`evals/run_eval.py::_normalise`), and the over-reaching assertion was corrected
to match its own question.

**Lesson:** when an eval fails, the first question is whether the expectation was
right. Fixing the system to satisfy a wrong test makes the system worse and the
test permanently misleading.

---

## 6. Synthetic data that failed its own verification

The first synthetic dataset was supposed to share nothing with the real extract.
Verification found 215 shared doctor names, 27 clinics, 31 chemists, and 2
overlapping `chair_id`s.

Nothing was copied. Common Indian names simply collide by chance against a large
real dataset, and the id range I picked happened to overlap at the edge.

**Fix:** id ranges offset above every real maximum (so collision is
arithmetically impossible, not just unlikely), and free-text values filtered
against the real values at generation time.

**Lesson:** "I didn't copy anything" is not the same as "nothing is shared", and
the difference is only visible if you check. The check is the deliverable.

---

## 7. The optimisation that wasn't where I assumed

Before touching performance, I measured. Turn latency was 7.7–12.1 s, of which
the **database was ~2.5%** and the model ~97.5%.

Then, inside that 2.5%, `run_sql` was taking 17–25 ms — while its actual SQL took
0.5–1 ms. The rest was the schema manifest being re-parsed from YAML on **every**
call to `queryable_columns()` and `pii_columns()`, both of which `run_sql` calls.
The guard was twenty times more expensive than the query it guarded.

**Fix:** cache the parse against the file's mtime. 17–25 ms → **0.10 ms**.

The wider point survived into the design: this is also why choosing Postgres over
an embedded columnar engine was fine despite Postgres being *slower* at wide
aggregates. Every query the app actually issues is an indexed lookup scoped to
one rep, and the database is a rounding error next to the model either way.
`/api/metrics` reports that split per deployment so nobody has to take my word
for it — or repeat my mistake of assuming.

**Lesson:** profile before optimising, then profile again inside the layer you
landed in. Both times I was wrong about where the time went.

---

## 8. `pkill -f` matching its own command line

Twice, `pkill -f "serve.py"` exited 144 — it had matched the shell running it and
killed itself.

Trivial, and included because it cost real minutes twice before I noticed. Kill
by explicit PID.

---

## 9. Calibrating the grounding check took three passes

The numeric grounding check compares every number in the answer against the tool
output from that turn. Simple idea; the calibration was not.

It fired on **2 of 6** consecutive answers that were all completely correct.
Three distinct false-positive classes, found by reading the audit log rather than
by anything failing:

1. **`95.0` vs `95`.** Postgres returns `DOUBLE PRECISION` as `95.0`; the model
   sensibly writes "95% target". Different strings, same number.
2. **`29974.8427` vs `₹29,974.84`.** The model rounded a currency value to two
   decimals — exactly what it should do for a human reader.
3. **A number inside a product name.** This one was a **true** positive: the
   model wrote "Osteovim 10 mg" when the data has only the brand name. The
   dosage was invented, and the check was right to say so.

So the fix had to loosen (1) and (2) without losing (3). The rule it settled on
is: **the model may reduce a number's displayed precision; it may not change the
number.** Source values are expanded into every rounded and truncated form from
0 to 4 decimals, and a claim matches if it equals any of them. `29974.84`
matches `29974.8427`; `31000` does not.

Result: 6 of 6 grounded on a re-run, with the invented-dosage case still caught
and pinned by a test.

**Lesson, and it is the point of this entry:** a warning that fires on correct
answers is worse than no warning, because it teaches the reader to dismiss the
one that matters. "Does it catch bad output" is only half the question. The other
half is "does it stay quiet on good output", and that half only shows up when you
read the logs of a system that is working.

---

## 10. The assistant was answering like a database console

**Found:** by asking it an ordinary question a curious user would ask.

> **"list down all the tables"**
> Available rep-scoped tables:
> `my_actual_visits`, `my_brands`, `my_chemists`, `my_doctor_codes`,
> `my_doctors`, `my_hooks`, `my_leaderboard_thresholds`, …

> **"show me the schema of my_reps"**
> | Column | Inferred type |
> | `chair_id` | Integer |
> | `rep_code` | Integer | …

Both answers are *correct* and both are wrong. The user is a medical
representative standing outside a doctor's chamber. `my_leaderboard_thresholds`
is not information to them, and "`chair_id` — Integer" is noise. It also
advertises the exact query surface to anyone who asks nicely.

**Root cause:** `build_instructions()` concatenated the full relation/column
listing into the system prompt. Put something in the system prompt and the model
treats it as part of what it knows about itself — so it recites it, helpfully,
on request.

**Fix, in three parts:**

1. **Move the listing out of the system prompt** and into the `run_sql` tool
   description. The model still has everything it needs to compose SQL; it just
   no longer reads the data model as a topic of conversation.
2. **Tell it what to say instead.** Not "refuse" — reframe. A question about
   tables is really a question about capability, so the rules now carry the
   answer to give: *"I don't work in tables — here's what I can help with:
   pre-call briefings, pending visits, brand performance…"*. And when the rep
   asks "what data do you have about me?", answer it properly — their name,
   code, cluster, current metrics — not the column names those came from.
3. **Measure the outcome.** A prompt rule can be talked around, so
   `guardrails.check_internal_disclosure()` scans each answer for scoped aliases
   and multi-word column names, and records hits to the audit log and
   `/api/metrics`. Three cases went into the eval gate as well.

**Result:** the same three questions now leak 0 identifiers (previously 5 and 3),
and — the part that matters — *"what data do you have about me?"* returns a
genuinely useful answer with real values instead of a schema.

**Lesson:** "correct" and "useful" are different tests, and only one of them
catches this. It is also a reminder that everything in the system prompt is
something the model may repeat — the prompt is not a private briefing.

---

## 11. One tool result put the whole page into a 300px horizontal scroll

**Symptom:** none, on a desktop. The frontend redesign shipped a `min-h-dvh` +
`grid-rows-[auto_1fr_auto]` shell to replace a four-link `h-full` chain. Typecheck
passed, lint passed, the build passed, and it looked correct at every width I had
actually opened.

**Found by** the one manual fixture the plan specified — a `run_sql` result of
40 rows × 8 wide columns at a 375px viewport — scripted against headless Chrome
rather than eyeballed. It reported `documentElement.scrollWidth - clientWidth = 300`. The whole page scrolled sideways.

**Cause.** Walking the ancestor chain upward from the table printed it plainly:

```
element                     width   min-width
div#tablewrap                 718        0px    <- overflow-x-auto, fine
div.relative.min-h-0          800       auto    <- the culprit
div.grid.min-w-0.flex-1       485        0px    <- correctly shrank
body                          485        0px
```

`div.relative.min-h-0` is a **grid item**, and grid items carry `min-width: auto`
— the automatic minimum size. It therefore refused to shrink below the min-content
width of its widest descendant, even though its own parent had correctly shrunk to
485px. The table's own `overflow-x-auto` was working perfectly; it just never got
the chance, because nothing above it would give up width.

**Fix:** `min-w-0` on all four grid items. Two lines of class text, zero visual
change at desktop widths, and the difference between a usable and an unusable
phone layout.

**Verified after:** 0px horizontal overflow at 320, 375, 768, 1024 and 1440, with
the table still scrolling inside its own row and shrinking with the viewport
(238px at 320 → 718px at 1440).

**Lesson:** the flex/grid minimum-size guards are the highest-risk classes in the
app precisely because they have *no visual signature* until content overflows.
They cannot be reviewed by looking; they have to be measured. This is also the
second time on this project that a passing build meant nothing about CSS — see
entry 12.

---

## 12. Tailwind never told me `brand-950` did not exist

**Symptom:** four references to a colour that was never defined, through a passing
build and green CI, silently dead in dark mode.

**Cause.** Tailwind emits nothing for a class it does not recognise, and does not
warn. There is no compiler step that sees class names: `tsc` treats them as
opaque strings, and the bundler is happy either way.

**Fix, in two parts.**

1. **Clear the default palette.** `@theme { --color-*: initial }` in
   `styles/theme.css` means `slate-500` and `brand-950` simply do not exist as
   classes any more.
2. **Make an unknown class an error.** `eslint-plugin-better-tailwindcss`'s
   `no-unknown-classes` reads the real v4 CSS entry point, so it validates against
   the actual token set. `npm run lint` — which was a declared script that had
   never once run, because neither eslint nor a config was installed — now runs in
   CI.

The same lint pass immediately found real debt the build had never mentioned:
`backdrop-blur` and `rounded` are deprecated v3 spellings under v4, `api.ts` was
reading `response.json()` as `any` and dereferencing it unchecked, and four
components were syncing state inside effects.

**Lesson:** "the build passes" is not a statement about CSS. A grep sweep was the
plan's proposed gate here; making the classes *not exist* is strictly stronger,
because it fails closed instead of relying on someone remembering to grep.

---

## 13. My own confidentiality gate passed by scanning nothing

**Symptom:** `check_no_vendor_terms.py --root ..` printed
`scanned 0 text files … PASS` and exited 0.

**Cause.** The script takes a **positional** path, not a `--root` flag. `--root`
was resolved as a directory name, `rglob` matched nothing, the hit list was
therefore empty, and empty was reported as success.

This is the worst failure mode a gate can have: it does not fail, it does not
error, it *reassures*. And the test suite had the same hole — the
"skips binary and vendor directories" test built a tree containing only skipped
files, so it asserted exit 0 over a zero-file scan and would have passed just as
happily if the scanner had walked nothing at all.

**Fix:** the script now exits non-zero if the root is not a directory, and if it
scanned zero files — "I proved nothing" is not a pass. The test grew one ordinary
file and an assertion on the scanned count.

**Verified:** the bad invocation now exits 1; a real scan covers 117 files across
backend *and* frontend and passes on merit.

**Lesson:** a check that can pass without running is worse than no check, because
it buys false confidence. Every gate should be tested by watching it *fail*, and
mine had only ever been watched succeeding.

---

## 14. Two UI bugs that only a driven browser could find

Both of these survived typecheck, lint, a clean build, and looking at the app.
Both were caught by scripting Chrome over CDP — logging in for real, clicking
the actual controls, and asserting on what the DOM then said.

**A. Escape cancelled a rename by calling `blur()`.** The handler was
`cancelledRef.current = true; e.currentTarget.blur()`, relying on the blur
handler to run the shared commit path and notice the flag. `blur()` on an
element that does not have focus is a silent no-op — so the row stayed stuck in
edit mode with no way out. It happened to work in manual use because `autoFocus`
had focused the input; the mechanism was one lost focus away from breaking.

Replaced with `finish(save: boolean)`, called directly from Enter, Escape and
blur, guarded by a ref so it runs exactly once. Focus-independent, and cancel is
no longer expressed as a special case of save.

**B. The theme toggle's selected icon was permanently upside-down.** The plan
called for the icon to rotate 180° on activation. I implemented it as
`active && 'rotate-180'` — a persistent class, not a transition. A crescent moon
held at 180° does not read as a moon; it reads as some other glyph entirely, and
the screenshot is the only reason I noticed. Fixed with a `swap` keyframe that
rotates in and *settles*, re-keyed on selection so it replays.

**Lesson:** static review and a screenshot cover different bugs, and neither
covers interaction. `blur()`-on-an-unfocused-element is invisible in the source
and invisible in a screenshot; it shows up the moment something presses the key.
The same probe run also confirmed the parts that were right — focus trap, body
scroll lock, `inert`, Escape and scrim-tap on the drawer; thumbnails as real
`blob:` object URLs inside the composer shell; and the tool timeline filling in
one row at a time as the calls actually completed.

---

## 15. Six questions asked before the LangGraph migration, and the one that mattered

The agent core is moving to LangGraph so that human-in-the-loop becomes possible:
`interrupt()` plus a checkpointer is the part that is genuinely painful to hand-roll, and
an approval gate ("send this to Dr Sharma?") is a real compliance need. RAG alone would not
have justified replacing a working core; HITL does.

Before writing any graph code, six unknowns were checked in a throwaway script — because
one of them could have changed the design and another turned out to be a live bug.

| # | Question                                                           | Answer                                                                                                                                                                                                                                                   |
| - | ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | Can`ChatOpenAI` pass **`prompt_cache_key` per request**? | **Yes.** `_get_request_payload` merges arbitrary kwargs and `_construct_responses_api_payload` renames but never whitelists, so the key survives; a live call accepted it. Works via `.bind()` and via an invoke kwarg.                      |
| 2 | `reasoning={"effort":"medium"}` with `use_responses_api=True`? | Yes — and`reasoning_effort` is auto-converted to `reasoning.effort`.                                                                                                                                                                                |
| 3 | `StructuredTool` from a raw **JSON-Schema dict**?          | Yes.`args_schema` accepts the dict, `ainvoke` dispatches to the async coroutine with kwargs, and `bind_tools(..., strict=True)` reproduces today's wire shape **including `strict: True`**. The adapter is ~20 lines and no tool changes.  |
| 4 | Is`usage_metadata.input_token_details.cache_read` populated?     | Yes, alongside`cache_creation`. `/api/metrics` keeps working.                                                                                                                                                                                        |
| 5 | Multimodal image shape?                                            | All three work (langchain standard block,`image_url`, raw `input_image`). Using the standard block for portability. An initial "not a valid image" failure was a corrupt hand-pasted test PNG, not a format problem — the fixture is now generated. |
| 6 | Can`AsyncPostgresSaver` target a **named schema**?         | **No, and this was the important one.** See below.                                                                                                                                                                                                 |

Question 1 was the one with a veto: `RepContext.cache_key()` partitions OpenAI's prompt
cache per rep so one rep's cached prefix can never be served to another. That is a security
property, not a performance tweak, and had langchain-openai been unable to plumb it
per-invocation the plan was a custom node calling the OpenAI client directly. It can, so
the standard path stands.

### Question 6: the default configuration would have caused two serious bugs at once

`AsyncPostgresSaver` has no schema parameter. It runs `CREATE TABLE IF NOT EXISTS checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations` **unqualified**,
so the tables land wherever `search_path` resolves. Our `DATABASE_URL` carries
`options=-csearch_path%3Dapp,public`, so `app` wins. Two consequences, both silent:

1. **State destroyed on every data reload.** `etl/load_postgres.py` does
   `DROP SCHEMA app CASCADE` and renames the freshly built schema over it. Every rep's
   entire conversation history would vanish on each `python -m etl.load_postgres`.
2. **A cross-rep history leak.** `pg_default_acl` for schema `app` is
   `{qorvexa_ro=r/shubham}` — the loader's `ALTER DEFAULT PRIVILEGES` means *any new table*
   created there automatically grants SELECT to the read-only role. The checkpoint tables
   hold full message content, and `run_sql`'s denylist is built from
   `schema.base_relations()` (the manifest), which would not know these names. So
   `SELECT * FROM checkpoints` would have passed the guard and returned every rep's
   transcripts.

This is the third time on this project that the same shape of bug has appeared: durable
state landing somewhere the read-only role can see it, invisible because the results still
look plausible. It is why `conversations` reads moved to the rw pool (entry 6) and why the
document corpus will get its own schema too.

**Fix:** a dedicated `agent` schema, created explicitly, with the checkpointer's connection
opened on `search_path=agent` and **no grants to `qorvexa_ro`**. The read-only role then
cannot reach conversation state structurally, rather than being blocked by a regex — and
the ETL never touches the schema, so a reload cannot destroy it.

**Lesson:** a library's default configuration is not neutral; it inherits your connection's
settings, and ours pointed straight at the one schema that gets dropped and the one that
auto-grants read access. Both bugs were free to find in a spike and would have been
expensive to find in production. The half hour that produced this table was the
highest-value half hour of the migration.

---

## 16. The migration passed the eval gate and still broke in the browser

The LangGraph swap was A/B'd exactly as planned: the same 13 golden cases run
through both cores, selected by `MR_BOT_ENGINE`, with both engines reached
through one dispatcher so the harness exercised what production would.

```
MR_BOT_ENGINE=responses   13/13
MR_BOT_ENGINE=langgraph   13/13
```

Behaviour-neutral on the gate. So the old engine could go — except that the gate
does not cover the path users take.

**The bug the eval could not see.** `evals/run_eval.py` drives the agent core
directly, with no HTTP server, and calls `load_dotenv()` on the way in. That put
`OPENAI_API_KEY` in `os.environ`. `ChatOpenAI` falls back to reading that
variable when no `api_key` is passed — so in the eval it silently found one.

Under uvicorn nothing calls `load_dotenv()`. The app reads its key through
pydantic-settings into `settings.openai_api_key` and never exports it, so the
same code raised `OpenAIError: Missing credentials` on the first real turn. The
existing `get_client()` had always passed the key explicitly; the new path
inherited a library default instead, and the fix is one keyword argument.

Caught by driving a real browser against a real server — log in, send a message,
assert the tool timeline and answer appear — which surfaced it as *"the timeline
is empty and there is no answer"* within a minute.

**What was verified before deleting the old engine**, because the gate alone was
not enough:

|                              | result                                                                                                                                                               |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| eval gate, both engines      | 13/13 each                                                                                                                                                           |
| HTTP + SSE path in a browser | timeline fills incrementally (0 rows → 1 at 3.5s → answer at 9.1s), caret lives and dies correctly, serif prose renders                                            |
| frontend changes required    | **none** — the SSE contract did not move, and `response_id` was already typed `string \| null`                                                             |
| multi-turn continuity        | turn 2 asked*"What are his brand priorities?"* with no name, and the graph resolved it from the thread. The eval is single-turn, so it could not have told us this |
| checkpoints                  | 89 rows across 16 threads, all in`agent`, keyed on the conversation uuid                                                                                           |

**Then deleted:** the hand-rolled loop, `_to_openai_tools`, `build_user_input`,
the `MR_BOT_ENGINE` flag and its dispatcher, `clear_response_id()`, and the
reading and writing of `previous_response_id` (the column stays nullable for one
release so a rollback has somewhere to land). `agent.py` keeps only what was
never loop-specific: the system prompt, `build_instructions`, `TurnResult`,
`ToolTrace` and the limits.

**Two behaviour changes worth stating rather than discovering:**

- **Tool calls in one round now run in parallel.** `ToolNode` executes them
  concurrently where the old loop was sequential, so several rows can appear
  in-flight at once. The UI keys on `call_id`, so it handles this — and it is
  faster — but the timeline no longer implies a strict order.
- **The round cap has to clean up after itself.** The old loop could simply
  `break`: continuity was an OpenAI handle, so an abandoned tool call was never
  re-sent. The graph owns the transcript, and OpenAI rejects a turn whose history
  contains a tool call with no output — so hitting the cap now answers its
  outstanding calls, or it would break the *next* message in that thread.
  `tests/test_hitl_seam.py::test_the_tool_round_cap_answers_its_pending_calls`
  asserts every requested call id has a matching output.

**Lesson:** an eval harness that bypasses the transport is measuring the model,
not the product. Both are worth measuring — but "13/13" and "a user can send a
message" are different claims, and only one of them was true for about an hour.

---

## 17. Three times retrieval "found nothing" while working perfectly

Retrieval over the product literature went in as Qdrant-only — vectors, text and
metadata in one store, no Postgres in the path, so no dual-write and no orphaned
vectors on delete. The failures were all the same shape, and none of them was in
the retriever.

**A. The chunk that did not know which drug it was about.**

Asked *"maximum Cardevia dose in renal impairment"*, the passage that literally
answers it was **not in the top 5**. The front-matter chunk won instead. The
reason, once the chunk was printed:

```
4.2.1 Renal impairment
eGFR 30-59 mL/min: maximum 20 mg once daily. eGFR below 30 mL/min: ...
```

It never says "Cardevia". Lexically there is no brand token to match;
semantically it is about eGFR, not about a brand. Meanwhile the front-matter
chunk repeats the brand name three times and answers nothing.

Fixed with a **contextual chunk header**: brand, molecule, title and section are
prepended to the text that gets embedded and lexically indexed, while
`payload["text"]` keeps the clean content for display and citation. Measured
before and after:

| query                   | before                            | after                                      |
| ----------------------- | --------------------------------- | ------------------------------------------ |
| Cardevia + metformin    | §1 Name of the medicinal product | **§4.5 Interaction, rank 1**        |
| max Cardevia renal dose | not in top 5                      | **§4.2.1 Renal impairment, rank 1** |

**B. A section label that would have produced a wrong citation.**

Gastroliv had no §4.5 chunk at all: the clopidogrel interaction table had been
absorbed into §4.3. The heading was extracted correctly — the chunker dropped it.
Gastroliv's §4.3 body is one line, under `MIN_CHARS`, so it merged forward, and
`carry_title or title` let the *carried* label win. The content was right and the
section number was wrong, so a rep would have cited a drug interaction as a
contraindication. Section-aware chunking exists to make citations checkable; a
confidently wrong citation is worse than none.

`MIN_CHARS` dropped from 60 to 25 — a one-line section is perfectly citable, and
now that every chunk carries a contextual header a short chunk is as retrievable
as a long one. Where a merge genuinely happens the label names both sections.

**C. Two filters that silently excluded the answer.**

A rep uploaded a territory brief for "Zephyrion". They could see it in
`/api/documents`. Asked about its dose, the assistant said the literature did not
cover it — twice, for two different reasons:

1. The model passed `brand="Zephyrion"`. Zephyrion is not in the known brand
   list, so the document's payload brand was null, and `brand` was a `must`
   filter. The filter excluded the only document that answered the question.
2. With that fixed, `doc_type="monograph"` excluded it again: the file is named
   `…-territory-brief.pdf`, so it is inferred as `brief`. The model was right to
   ask for clinical facts; the heuristic classification disagreed.

Both times, querying the retriever directly returned all four chunks ranked top.
The model saw an empty result set and reported an empty corpus.

**The rule adopted:** *a model-supplied narrowing may steer ranking; it must never
be able to empty the result set.* brand, molecule and doc_type are now folded into
the query text — the chunk header carries all three, so both legs still respond to
them — and `_scope_filter()` does nothing but tenancy. Retrieval scores were
unchanged by the removal (recall@1 92.3%, recall@5 100%), so the filters had been
buying nothing and costing correctness.

That also leaves the security story single-purpose: one filter, one job. With
Qdrant as the only store there is no SQL backstop, so the fewer things that
predicate does, the easier it is to be sure of.

**Lesson:** "no results" is the most misleading thing a retrieval system can say,
because it is indistinguishable from an empty corpus. Every one of these was found
by querying the retriever directly and comparing against what the model saw — a
diagnostic worth reaching for before touching the ranking.

---

## 18. The retrieval eval was wrong twice before the retriever was

`evals/run_rag_eval.py` scores recall@1, recall@5 and MRR against 27 golden
questions. Both the corpus embeddings (float16, 509 KB) and the golden query
embeddings are committed, so it runs **offline with no API key** — including from
a fork — which is the only reason it can be a gate on every pull request rather
than something that runs where a secret exists.

It was worth building for the failures in entry 17. It was also wrong twice, and
both corrections are more interesting than the passes.

**A. Two "expect none" cases that were not absences at all.**

*"Is Cardevia safe in pregnancy?"* was written as `expect_none`, on the grounds
that Cardevia's monograph deliberately has no section 4.6. Retrieval returned the
detailing guide's "4 What must not be said" at the top score, and the eval called
that a failure. Reading the passage: *"No use in pregnancy, paediatrics, or any
indication outside section 4.1."*

That is the **ideal** result. It tells the rep both that there is no data and that
they are not permitted to discuss it. The premise was wrong, not the retriever.
Both cases were converted, and a genuinely absent one added — the elimination
half-life of Cardevastatin, absent because no monograph in the corpus has a
pharmacokinetics section, verified by grep rather than assumed.

**B. Absence cannot be measured by score.**

The replacement case still failed. Asked for a half-life, retrieval returned
Cardevia's composition section at 0.64 — because it is the closest thing that
exists. The threshold was the wrong instrument twice over: **a retriever cannot
say "I don't know"**, it always returns nearest neighbours; and RRF scores are
rank-based and relative, so 1.0 means "top of both legs", not "confidently
relevant".

Absence is now asserted by **content**: do the retrieved passages contain the
concept at all? If they do not, the model has what it needs to refuse — and
whether it actually refuses is asserted end-to-end in `evals/golden_rag.yaml`,
which is where a judgement about wording belongs.

**Current, over 183 chunks:** recall@1 92.3%, recall@5 100%, MRR 0.962.

One known weakness, recorded rather than hidden: *"When must Cardevia not be used
at all?"* retrieves the detailing guide's compliance list ("what must not be
**said**") ahead of the clinical contraindications, and §4.3 does not make the top
5. The lexical overlap on "must not be" misleads both legs. The golden case uses
the direct phrasing and the paraphrase failure is noted in the file.

**C. And once more, in the end-to-end gate.**

The same mistake, in `golden_rag.yaml`. The objection-handling case carried
`expect_not_contains: ['superior to']`, on the grounds that the detailing guide
forbids comparative superiority claims. It failed on an answer that was, on
reading it, close to ideal:

> "Avoid claiming superiority or making an unsupported price comparison with a
> named competitor." *[Cardevia Detailing Guide — What must not be said, page 1]*

The model had surfaced the prohibition *to the rep*, with a citation. A substring
ban cannot tell making a claim from forbidding one. The assertion is now positive
— the answer must reference the prohibition — and whether a superiority claim is
ever actually made is left to human review, because that is a judgement a
substring check is not capable of making.

**Lesson:** a golden set is code, and it is wrong in the same ways code is wrong.
Three of the failures across these two eval suites were my assertions rather than
the system — and in every case the "failing" behaviour was the behaviour I wanted.
That is an argument for writing the eval early and reading each failure properly,
not for trusting it. Negative assertions are the worst offenders: "this string
must not appear" cannot express "this claim must not be made".

---

## 19. The eval harness forgot to open the vector store — for the second time

Every retrieval case in the LLM eval failed, and every failing answer said some
version of *"the approved product literature is currently unavailable"*. Four
cases, four polite refusals, and the model was behaving perfectly: retrieval
genuinely was unavailable.

`evals/run_eval.py` drives the agent core directly — no HTTP, no server — which
is a property worth having. But it means the harness does not run the FastAPI
lifespan, so every resource the app opens at startup must be opened by the
harness too. It opened the database pools and the checkpointer. It never called
`open_vectors()`.

**This is the same bug as entry 16.** There, the eval found `OPENAI_API_KEY` in
the environment because it calls `load_dotenv()` and uvicorn does not, so the
LangGraph migration passed 13/13 and then failed on the first real turn. Here it
is the reverse direction — the app opened something the harness did not — and the
symptom was worse, because "retrieval returned nothing" is indistinguishable from
"the corpus is empty".

I also misdiagnosed it once: the first failing run coincided with me rebuilding
the Qdrant store, and Qdrant's local mode is single-process, so I blamed the lock
and re-ran. The second run failed identically with nothing else touching the
store, which is what made it obvious the harness had never opened it at all.

**Fixed at the cause.** `app/bootstrap.py` now has one `open_resources()` /
`close_resources()` pair, used by both the FastAPI lifespan and the eval harness.
Two call sites listing resources by hand is one list too many. The `audit=False`
flag is the only difference between them — the eval has no requests to attribute.

**Lesson:** "the agent core is transport-agnostic and the harness drives it
directly" is a real architectural benefit and a standing source of this exact
bug. The benefit is worth keeping; the mitigation is that resource setup must be
a shared function rather than a convention followed in two places. Twice was
enough.

## 20. Four measurements that killed the obvious design, and a crash nobody could reach

The agenda feature needed a second and third agent, and the obvious LangGraph
shape is a compiled subgraph per agent. Before building it I ran the questions
against the installed `langgraph==1.2.11` — reading the package source, then
executing in-memory probes with `InMemorySaver` and no API key. Four answers:

1. **`interrupt()` inside a subgraph does reach the parent thread**, and a
   parent-level `Command(resume=…)` does resume it. `GraphInterrupt` is
   suppressed only at the root (`if isinstance(exc_value, GraphInterrupt) and not self.is_nested`); a nested loop re-raises. So far, so good.
2. **A parent-level resume cannot rewrite a subgraph's pending tool arguments.**
   Not "with difficulty" — silently. The subgraph resumes from its own nested
   checkpoint and the parent's channel update never reaches it; the probe's node
   re-ran with the *original* arguments and the final state kept them.
3. **A subgraph's token deltas never reach the stream reader** without
   `subgraphs=True` (`StreamMessagesHandler.on_chat_model_start` drops nested
   namespaces outright). What arrives instead is one batched message whose
   `langgraph_node` is the *parent* node's name.
4. **`subgraphs=True` changes the yielded tuple's arity** from `(mode, payload)`
   to `(ns, mode, payload)`, which breaks every branch of the reader.

(2) is the one that mattered. The user had asked for approve-**or-edit**-then-send:
the rep rewrites a draft in the approval card before approving it. With a
subgraph, the edit would have been discarded and **the original draft sent** —
while the card showed the edit. In a tool that emails prescribers that is not a
bug you ship, and it is invisible in casual testing because the send succeeds.

So the three agents are three *nodes in one graph*. What actually distinguishes
an agent here is its instructions, the tools it may call, and whether its output
is reviewed — all three are per-node, and the shared message channel is a feature
rather than a compromise: when the rep says *"email the doctor you just briefed me
on"*, the agenda agent needs the orchestrator's briefing in its context.

**Agent-as-tool was disqualified separately, and mechanically.** `GraphInterrupt`
inherits from `Exception`, and `tool_adapter._wrap` catches `Exception` to turn
every failure into `{"error": …}` — a deliberate convention, so the model can read
and explain its own tool failures. Executed, the pause became
`{"error": "send_email failed: ()"}`. The only fix would be a carve-out inside the
one module whose entire purpose is that errors are returned and never raised.

### The crash that nobody could reach

While mapping the reader I found a live bug on the exact path this feature walks.
`run_turn`'s `updates` loop did this for every key in the payload:

```python
for node, delta in (payload or {}).items():
    for message in (delta or {}).get("messages", []) or []:
```

On an interrupt, `node` is `"__interrupt__"` and `delta` is a **tuple** of
`Interrupt` objects. `tuple` has no `.get`, so the first real interrupt would
raise `AttributeError`, escape into `chat.py`'s error path, and reach the rep as
the generic *"Something went wrong handling that message."* — **while leaving the
conversation wedged at a pending interrupt with no way to resume it.** Every
subsequent message in that thread would interrupt again immediately.

It had never fired because no registered tool was gated, which is exactly why
`tests/test_hitl_seam.py` existed and exactly what it could not catch: it drove
`build_graph` directly and never went through `run_turn`. The fix is one branch,
placed first — and `run_turn` now accepts an `llm`, so the transport is testable
with a scripted fake. That seam is what `evals/test_agenda_resume.py` uses to
assert the whole round trip over real HTTP.

### Two smaller ones, both found by tests that were about something else

**`check_citations`' shortcut is backwards for outbound text.** It returns
`cited: True` when nothing was retrieved, on the sound reasoning that if the
corpus held nothing, the honest answer has no citation in it. Reusing it for a
draft email inverted the rule: a clinical claim with *no* retrieved passage is
the **invented** claim, the one case most worth stopping, and it was sailing
through as compliant. Outbound now treats an empty retrieval as untraceable
rather than exempt — and the refusal exemption had to be re-applied explicitly,
because `check_citations` returns early before it looks at those markers.

**`schema.get("items") or {}` is an infinite regress.** Making the forbidden-
parameter check recursive (so a `mailbox` nested inside `create_event`'s
`attendees` array cannot pass) introduced it: an absent key becomes `{}`, `{}` is
a dict, and `{}` has an absent key. It was caught by
`test_duplicate_tool_name_is_rejected`, a test with nothing to do with schemas —
it just happened to build a real tool list. Recursing only into keys that are
actually present fixes it.

**Lesson:** the two hours of probing before writing the graph were worth more than
the graph. Three of the four findings are things the documentation does not say
and that no test of mine would have failed on — the edit would simply have gone to
the wrong place, quietly, in production.

## 21. Two error shapes, one reserved word, and a guard that defeated itself

The Google agenda was built and passing. Then a question — *"where do I get the
client ID for each user?"* — turned into three bugs, none of which any existing
test could have failed on.

**There is no per-user client ID, and the real question was never in the code.**
One OAuth client identifies the *application*; each rep's consent yields a per-rep
refresh token. What was undocumented is that **who may click Connect is a Google
console setting** with four options and wildly different prices: Testing (100
addresses you list by hand, tokens expire in **7 days**), Internal (a Workspace
domain, unlimited, no verification), Published (any account, but every
mail-*reading* scope is "restricted" and needs a **CASA security assessment
renewed every 12 months**). For a pharma field force the answer is Internal, which
turns "fund a security audit" into "ask the Workspace admin". That belonged in a
doc, not in a header comment.

**A dead connection reported itself as connected, forever.** In Testing audience
the refresh token expires weekly, and `refresh_access_token` did not distinguish
`invalid_grant` from anything else. So the row stayed, Settings showed a green
badge, and every mail tool returned `"Google returned 400."` indefinitely. Not a
demo-only path: a rep revoking access or changing their password lands in exactly
the same state.

Fixing it needed the error code, and the code was being **thrown away twice**.
`client.request` parsed `(json()["error"] or {}).get("message")` — the REST shape.
The OAuth token endpoint follows RFC 6749, where `error` is a bare **string**, so
`.get("message")` raised `AttributeError` on a `str`, which was caught, so `reason`
was empty and every token failure collapsed to the same sentence.

**The narrow branch is the load-bearing part.** Only `invalid_grant` may delete a
token. `invalid_client` means the *operator's* secret is wrong, and treating it as
a dead grant would wipe **every rep's** credential across the deployment on the
first request after a bad deploy — consent cannot be restored server-side, so all
25 reps would reconnect by hand. That distinction is one `if`, and it has its own
regression test.

**A guard that crashed the thing it protected.** Writing a test for the send path
surfaced `KeyError: "Attempt to overwrite 'thread' in LogRecord"`. `thread` is a
**reserved LogRecord attribute** (the OS thread id), and `logging` raises on a
collision — so `extra={"thread": thread_id}` never logged a slightly wrong field,
it *raised*. And it sat inside:

```python
except GoogleError:
    # One unreadable thread must not lose the whole triage list.
    log.warning("could not read thread", extra={"thread": thread_id})
```

The handler whose entire purpose was surviving one unreadable thread would have
crashed the **whole** triage list the first time one appeared. It survived review
because the happy path never logs it, and it was found by a test about something
else. `tests/test_logging_extras.py` now greps for the collision, and proves
`logging` really does refuse.

**Failing open on the safety-critical rule.** The same test found that `send_mail`
swallowed a thread-read failure and continued with `thread_text=""`. That reads
like graceful degradation and is the opposite: `check_outbound` uses the thread to
decide whether it is an **adverse-event report**, so an empty thread means the
pharmacovigilance routing rules never fire. A Gmail hiccup could have turned *"do
not comment on cause"* into a sent reply doing exactly that. It now refuses the
send. **When the missing input is what makes a check strict, degrading is not
graceful.**

**An ordering bug in a migration that only bit once.** The new index covers
`due_time`, and it was written above the `ALTER TABLE ... ADD COLUMN`. On a fresh
database that works; on an existing one `DROP INDEX` succeeded and `CREATE INDEX`
failed on the missing column — leaving the table **unindexed** until someone ran
the file a second time. Caught by running `psql -f` twice from a dropped-index
state, which is now how it is checked.

**And one substring ban, again.** My own injection test asserted
`" from:" not in q` — which fails on the *correct* output, because `from:` does
appear, inside the quotes, where Gmail reads it as text. Third time this shape of
mistake has appeared here (`expect_not_contains: 'superior to'`, then `"declined" not in ...`). A substring ban cannot tell a smuggled operator from a quoted
literal. The assertion is now positional: strip the quoted spans and check what is
left is only what we intended.

**Lesson:** every one of these was found by writing a test for something adjacent.
None was reachable from the happy path, and two of them — the reserved log key and
the fail-open send — were *inside error handlers*, which is the code least likely
to run in review and most likely to run in production.

## 22. The compliant clinical email could never send — and the audit column that always said False

A code review found that the flagship compliant flow — retrieve literature, draft
a cited clinical email, pass the reviewer, get the rep's approval — could never
actually reach Gmail. `services/agenda.send_mail` re-runs `check_outbound` on the
final bytes (correct, invariant 1.10), and that check deliberately treats "no
passages retrieved" as *every clinical claim is uncited* rather than as an
exemption (also correct — an invented claim is the case that most needs
stopping). But the `send_email` tool handler **never passed the turn's passages
to the service**, so the final check always ran with `retrieved=[]` and blocked
any draft containing a clinical term plus a figure — including the very draft the
reviewer had just cleared against those passages. Two rules, each right in
isolation, composed into a feature that was structurally dead. It failed
*closed*, which is why nothing looked broken: every block read as the compliance
check doing its job.

The fix could not be a tool parameter — the model composes tool arguments, and
"what was retrieved this turn" is not the model's to assert (the same reasoning
as chair_id, invariant 1.2). It could not be graph state either, because handlers
never see state; ToolNode hands them only their schema arguments. So it travels
**out-of-band**: the graph's tools node mines the passages from the transcript
(`_literature_in`, the function the reviewer already used) and sets a
`ContextVar` immediately before dispatching; the gated handlers read it back.
ContextVars propagate into the child tasks ToolNode creates because children copy
the context at creation — which happens after the set — and each graph invocation
runs in its own context, so concurrent reps cannot see each other's values. See
`app/bot/approval_context.py`.

The same channel fixed a second lie: `agenda.outbound_log.edited_by_rep` was
hardcoded `False` at every call site — in the one artefact whose entire purpose
is "what was sent, and did a human change it". The approval node now reports
which call ids `_apply_edits` actually rewrote (it must, because after the
rewrite the transcript no longer knows the original arguments), and the flag
rides the same ContextVar into the handlers. It is round-level rather than
per-call — a handler does not know its own call id, and the agenda prompt already
requires a gated call to be issued alone — and that imprecision is documented
where the variable lives, so nobody "fixes" it by adding a call-id parameter.

**Lesson:** two checks that each fail closed can compose into a feature that
always fails. The absence-of-passages rule was tested, the final-bytes re-check
was tested — what no test drove was the *path between them*, because the eval
harness stops at the graph and the unit tests stubbed the service. The new
`tests/test_approval_context.py` drives the real graph into the real service
signature and asserts what the service received, which is the only place this
class of bug is visible.

## 23. Two guards that were right about the rule and wrong about the world

Both of these were reported as "it says no but it should say yes", and in both
cases the code was doing exactly what it was written to do.

**The allowlist that only remembered people who wrote first.** A new mail needs a
recipient the rep has "already corresponded with" (invariant 1.10) — the control
that does not depend on the model obeying "treat mail as data". It was built from
`TriageItem.from_address`, which is the **counterparty's** address. A thread the
rep wrote that nobody answered has no counterparty at all: `from_address` is the
empty string and the filter drops it. So the set meant *people who have written
to me*, while the refusal it produced said "not someone this rep has corresponded
with" and the tool description promised "an address the rep has already
corresponded with". Measured on the real mailbox that reported it: **5 of the 8
threads in the window** were the rep writing with no reply, the allowlist held 3
addresses instead of 15, and the address they were trying to write to was one of
the ones thrown away. `correspondents()` now reads both directions of each
thread, over its own wider window with its own cache. The security property is
untouched — an address that appears in no thread of the rep's own is still
refused, which is the whole point of the guard.

Worse than the block was the **escape hatch that did not exist**. Both refusals
ended with "or ask the rep to confirm the address", so the model dutifully asked,
the rep confirmed, the model retried, and the identical refusal came back. A
guidance string had promised a capability the system does not have, and the
resulting loop looked like the assistant being stupid rather than the message
being wrong. The strings now say what is actually true: send the first mail from
Gmail, or create the meeting with `notify=false`.

**The redirect that resolved against the wrong origin.** After a successful Gmail
consent round trip the rep landed on `{"detail":"Not Found"}`. The callback ended
with `RedirectResponse("/?agenda=connected")` — relative, so the browser resolved
it against the **API** (uvicorn on :8000), which serves no page. The credential
had been stored perfectly; only the landing was wrong. That is the worst shape a
bug can take from outside: everything worked and the last thing you saw was a
404, so the natural conclusion is that the connection failed. It now redirects to
an absolute origin read from the CORS setting — already required, already correct
in both shapes — and lands on Settings, the page the rep left and the only one
that shows the result.

**A third, found while fixing the first two.** `tests/test_token_crypto.py`'s
"a half-configured agenda must be refused" started failing — but only in the full
suite, never alone. `etl/ingest_docs.py` calls `load_dotenv()` at **import** time,
and the upload endpoint imports it inside the handler, so the first test to drive
that endpoint put the developer's entire `backend/.env` into `os.environ` for the
rest of the session. On a machine where Google *is* configured, the third value
the validator test needs to be ABSENT was being supplied from the dotenv. Fixed
at the class rather than the case: `tests/conftest.py` now restores `os.environ`
around every test.

**Lesson:** all three were invisible to CI by construction. CI has no `.env`, so
the env leak had nothing to leak; CI never completes an OAuth round trip, so the
relative redirect never resolved; and CI's mailbox is a fixture where everyone
writes first. A guard can be perfectly tested against the world the tests build
and still be wrong about the one it ships into.
