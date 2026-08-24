# The synthetic dataset

`backend/etl/seed_app.sql` — 10 tables + 5 views, 12,814 rows. Everything about a
fictional **Qorvexa Healthcare**.

The dataset ships as SQL rather than as the 9 parquet files it was built from.
The parquet are not in the repository; the seed is, and it is what `setup.sh`,
`docker-compose.yml` and CI apply. Besides the rows it carries the `qorvexa_ro`
role and the column-level grant that withholds `app.reps.password_hash` — those
are part of the dataset's contract, not of the loader, because the read-only role
is the boundary the whole tool layer depends on
(see [docs/claude/security-invariants.md](docs/claude/security-invariants.md) §1.5).

It contains **no conversations or messages**. Chat history lives in `public` and
is created empty by `etl/chat_history.sql`.

## Provenance, stated plainly

This dataset stands in for a confidential production extract that this
repository has never contained and never will.

The generator that produced it read that real extract — not to copy anything
from it, but to **guarantee disjointness**: it loaded every real doctor name,
clinic and chemist (roughly 470k / 70k / 275k distinct values) and drew only
from candidates absent from those sets. Id ranges were offset above every real
maximum, so a shared id is arithmetically impossible rather than merely unlikely.

That mattered. The **first** version of this dataset failed its own verification:
common Indian names collide by sheer chance against a large real dataset, and 215
names, 27 clinics, 31 chemists and 2 chair_ids turned out to be shared. The
filter and the id offsets are what fixed it, and a verification script is why it
was caught rather than shipped.

**The consequence, stated honestly:** the generator cannot run in this repository.
Removing the exclusion filter changes the length of the candidate pools, which
shifts the seeded RNG stream, which changes every name and number it produces. It
would generate *a* valid synthetic dataset — just not *this* one.

So the generator was not carried over. Shipping a script that silently produces
different data than the committed data would be worse than not shipping it.
**`etl/seed_app.sql` is the source of record**, and it cannot be regenerated from
anything inside this repository — treat it as data, not as a build artefact.

## What is in it

| Table | Rows | Notes |
|---|---:|---|
| `doctors` | 938 | from 950 source rows — 12 stale-vintage rows dropped at load |
| `visits` | 3,323 | `actual` and `planned` merged behind a `visit_type` discriminator |
| `brands` | 2,834 | invented brand names, priority `P1`..`P6` |
| `hooks` | 2,340 | engagement talking points, free text |
| `required_pending_visits` | 1,876 | per doctor per month |
| `chemists` | 1,414 | tagged to doctors |
| `rep_metrics` | 50 | MCR coverage, visit frequency, calls/day |
| `reps` | 25 | the login identities |
| `targets` | 5 | per-SBU bands + company-wide thresholds |
| `data_vintage` | 9 | build metadata, surfaced as "Data as of …" |

Plus five **compatibility views** — `doctor_codes`, `actual_visits`,
`planned_visits`, `thresholds`, `leaderboard_thresholds` — which reconstruct the
pre-merge table shapes the tool SQL names. Each view **declares its columns** in
`manifest.yaml`, and the loader asserts the created view actually exposes exactly
those columns, failing the load if the two drift apart.

### Why 9 tables and not 13

A column-usage audit of every file that touches the data found:

- one table referenced by nothing at all,
- **36 columns referenced by zero code** — most strikingly `chair_doctor_key`,
  present on all seven tables that carried it and used by nothing, because every
  query joins on `chair_id` + `doctor_id` separately.

Dropping the dead weight reached 12 tables. Three merges (`doctor_codes` into
`doctors`, the two visit tables into `visits`, the two threshold tables into
`targets`) reached 9. `email` and `dr_address` were dropped; **`mobile` was
deliberately kept**, with synthetic numbers, so the PII guardrail has something
real to guard and stays testable.

## The quirks are the point

The data is not clean, on purpose. Each defect below exercises a specific code
path, and `etl/verify_data.py` asserts they are all still present — a dataset
that quietly became tidy would make the ETL and the app untested.

| Quirk | What it exercises |
|---|---|
| Numerics arrive as **strings** | the guarded cast path (`pg_input_is_valid`) |
| Missing values are the literal text `"null"` | the `null_sentinel` → `NULLIF` transform |
| Doctor names are **double-spaced** | `name_norm` generation, and fuzzy matching against it |
| Source column casing is inconsistent across files | the manifest's exact per-file key mapping |
| Some `doctor_name` values are genuinely NULL | the case that once crashed the fuzzy matcher |
| **A `doctor_id` appears under more than one chair** | why `chair_id` scoping is mandatory, not decorative |
| Two `load_date` vintages across tables | the vintage filter, and the mixed-vintage banner |
| 12 stale rows in the doctors file | the vintage filter actually dropping something (950 → 938) |
| An ISO timestamp string in a date column | the timestamptz-then-date cast |
| Apostrophes in free-text notes | literal escaping through the whole pipeline |

## Changing the schema

Edit `backend/etl/manifest.yaml`. Never alter the database directly: the manifest
also generates the column glossary the model is given and the scoped CTEs
`run_sql` uses, so a column added out-of-band is a column the app cannot see.
See [CLAUDE.md](CLAUDE.md) §2.

Applying that change to the data needs the parquet sources, which are not in the
repository — `python -m etl.load_postgres` exits with instructions if they are
absent. With them present:

```bash
python -m etl.load_postgres                 # manifest -> schema `app`
pg_dump "$DATABASE_URL" --schema=app --no-owner --no-comments \
    --quote-all-identifiers >> etl/seed_app.sql   # re-cut the seed
```

Keep the hand-written header and trailer of `etl/seed_app.sql` when you do:
`pg_dump` cannot emit `CREATE ROLE` or `ALTER DEFAULT PRIVILEGES`, so those two
blocks are maintained by hand and the file is unusable without them.

Then strip what newer tools emit that older ones reject (both broke CI once):
the `\restrict`/`\unrestrict` lines pg_dump 18+ adds (an older psql client
treats them as invalid commands, fatal under `ON_ERROR_STOP`) and
`SET transaction_timeout = 0;` (a PostgreSQL 17+-only parameter):

```bash
sed -i -E '/^\\(restrict|unrestrict) /d; /^SET transaction_timeout = 0;$/d' etl/seed_app.sql
sed -i '/ALTER DEFAULT PRIVILEGES FOR ROLE/d' etl/seed_app.sql  # names YOUR local role
```

`python -m etl.load_postgres --dry-run` prints the DDL and needs no data at all,
which is the quick way to check a manifest edit.

---

## The literature corpus (retrieval)

`backend/data/literature/` holds the documents the RAG feature retrieves from:
**15 product monographs, 3 detailing aids and 1 SOP** — 19 files, 183 chunks.

Only the rendered PDF/DOCX are committed. `python -m etl.generate_literature`
also writes a `.md` source beside each one, and those are git-ignored: ingestion
accepts `.pdf` and `.docx` only, so the Markdown would be a second copy of
content already in the repository.

Everything in it is **invented**, for two separate reasons. Real prescribing
information is somebody's copyright; and an assistant that answers dosing
questions out of a demonstration corpus must never be mistakable for a real
clinical tool. Every rendered page carries that notice, and the parser strips it
so it does not pollute the index.

| | |
|---|---|
| Brands | The same 15 as the structured data (`Cardevia`, `Hepatoval`, `Thyrolen`, …) |
| Molecules | One invented molecule per brand, all distinct (`Cardevastatin`, `Hepatovaline`, …) |
| Structure | SmPC section numbering — 4.1 indications, 4.2 posology, 4.2.1 renal, 4.3 contraindications, 4.5 interactions, 4.8 adverse effects, 6.4 storage |
| Source of record | Markdown, committed — diffable in a pull request |
| Also committed | The rendered `.pdf` / `.docx`, so CI parses real binary files rather than a convenient text fixture |

### Why it is differentiated rather than templated

A corpus where every monograph says the same thing with a different name makes
retrieval evaluation meaningless: every query matches everything equally and
recall@5 measures nothing. So each brand has its own molecule, drug class, renal
threshold, interaction set and adverse-event profile.

### Two deliberate omissions

- **14 of the 15 monographs have no section 4.6 (pregnancy).** That absence is
  what makes "the approved literature does not cover it" a testable behaviour
  rather than a prompt instruction — a real refusal needs a real gap.
- **No monograph has a pharmacokinetics section.** So "what is the elimination
  half-life of Cardevastatin?" has no answer anywhere, verified by grep. It is
  the case both the retrieval eval and the LLM eval are built on.

### The dosing tables are there on purpose

pypdf — chosen over PyMuPDF on licence grounds, see `requirements.txt` — is
weakest on tables, and a monograph's dosing table is the most safety-relevant
thing in it. So the corpus contains real tables, the ingest report prints
per-page character counts and flags thin pages, and extraction was **measured**
rather than assumed: row associations survive (`Adults, initial → 10 mg once
daily → Evening dose`), as do renal thresholds and full interaction rows.
`pdfplumber` (MIT) is the documented escalation if that ever stops being true.

### Your own documents

`backend/data/literature.local/` is gitignored. Ingest real files with

```bash
python -m etl.ingest_docs data/literature.local --scope chair --chair-id <id>
```

and they stay on your machine. The ingester also **refuses** any path under
`raw/`, mirroring the same refusal in `etl/export_postgres.py`: the confidential
extract is not ours to embed.

### Commands

```bash
python -m etl.generate_literature                    # Markdown -> PDF/DOCX
python -m etl.ingest_docs data/literature --scope global
python -m etl.ingest_docs data/literature --scope global \
    --embeddings-from evals/rag_corpus_vectors.npz   # offline, no API key
python -m evals.run_rag_eval                         # recall@5 / MRR, offline
```


---

## The agenda holds no synthetic data, and that is deliberate

The literature corpus is synthetic and committed (see above). The agenda is not:
Gmail and Google Calendar **are** the store, so there is no synthetic mailbox in
this repository and no fixture inbox behind the product.

Three consequences worth stating:

* **Nothing about mail is generated or seeded.** The only agenda rows Postgres
  holds are a rep's encrypted Google connection, their own tasks, and the log of
  what they approved and sent. Triage categories are derived from live thread
  structure on request; they are not stored, which is also why `gmail.modify` is
  not requested.

* **A real inbox is real data, so nothing from one is ever committed.** Test
  fixtures under `backend/tests/fixtures/gmail/` are hand-authored to the Gmail
  API's shape, using the same fictional brands as the corpus, and they pass
  `check_no_vendor_terms.py` like every other committed text file. Anything
  captured from an actual mailbox goes in `backend/tests/fixtures/gmail.local/`,
  which is gitignored — the same rule as `data/literature.local/`.

* **Doctors still have no email address, and that is unchanged.** `email` and
  `dr_address` were dropped at load and `etl/verify_data.py` asserts they stay
  gone (scoped to `table_schema='app'`, so the new schema does not collide). So a
  mail thread is joined to a doctor **by sender name**, through the same
  `resolve.find_doctor_candidates` rapidfuzz path the `find_doctor` tool uses, and
  only when the match is unambiguous. An ambiguous thread still appears in triage;
  it simply cannot contribute to the daily plan, whose merge key is `doctor_id`.
  Guessing would brief the rep on the wrong doctor, which is precisely what
  `resolve.py` exists to prevent.

  This also decides what `schedule_task` can do. With no address to invite, putting
  a task on the calendar creates a **private time-block with no attendees** — it is
  still gated, because it writes to a calendar the rep carries on their phone, but
  it cannot notify anyone. Inviting a real person stays with `create_event`, whose
  recipient the rep sees and approves.

### The task columns, and why time is separate from date

`agenda.tasks` carries `due_date date` and `due_time time` as two nullable
columns rather than one `timestamptz`. "Friday" and "Friday 4 pm" are genuinely
different states, and a timestamp forces you to invent the missing half — which
would show a rep a deadline they never set. A `CHECK` enforces that a time cannot
exist without a date, since a time alone means nothing.

It is also the honest choice given the timezone: tasks work with **no Google
connection at all**, so `calendar_tz` may not exist, and guessing a zone to
normalise a timestamp would store the wrong hour. Instead the zone is applied at
*read* time when deciding which section a task falls in — a connected account's
`calendar_tz`, else `AGENDA_TIMEZONE`. The same all-day/timed distinction the
calendar code already draws for events.

`important` is a boolean, not a 1–5 priority: a scale nobody calibrates is a
column full of 3s. `calendar_event_id` is plain text with no constraint, because it
is Google's opaque id and means nothing here.
