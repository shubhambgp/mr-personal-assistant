# MR Personal Assistant

An AI assistant for pharmaceutical **medical representatives (MRs)** — ask about
your doctors, visits, targets and brand performance in plain language, get
answers grounded in approved product literature, manage your day (Gmail +
Google Calendar + tasks), and send **nothing** to a prescriber without a
human-approval gate and an automated compliance review.

- **Backend:** FastAPI · LangGraph (multi-agent, human-in-the-loop) · PostgreSQL · Qdrant (hybrid RAG)
- **Frontend:** React 19 · TypeScript (strict) · Tailwind v4 · Vite · SSE streaming
- **Safety:** per-rep tenancy enforced mechanically · read-only SQL role ·
  approval-gated outbound mail/calendar · deterministic + LLM compliance review

> Demo data is fully synthetic — a fictional company, no real doctors or reps.

---

## 1. Prerequisites

Pick ONE way to run it:

| | Local | Docker |
|---|---|---|
| Needs | Python 3.12+ · Node 20+ · PostgreSQL 16+ | Docker Desktop (macOS/Windows) or `docker` + `compose` (Linux) |
| App URL | http://localhost:5173 | http://localhost:8080 |
| Best for | development | trying it out, demos |

Either way it is the same two commands — `git clone`, then `./setup.sh`. The
script announces every step, and ends with the app **running** — nothing left
for you to launch. It auto-picks Docker when Docker is installed and running,
local otherwise (`--local` / `--docker` force either).

An **OpenAI API key** is needed for chat answers. The app starts and the UI
works without one (the demo corpus was embedded offline), but the assistant
cannot answer.

**Windows:** run everything below from *Git Bash* or *WSL*.

## 2. Database

The full demo dataset ships in the repo as one SQL file —
**[`backend/etl/seed_app.sql`](backend/etl/seed_app.sql)** (10 tables + 5 views,
12,814 rows: 25 reps, 938 doctors, visits, brands, chemists). It also creates
`qorvexa_ro`, the read-only role the AI's SQL runs as. Three companion files
create empty schemas: `chat_history.sql`, `agent_schema.sql`, `agenda_schema.sql`.

You normally never apply these by hand:

- **Local** — `setup.sh` applies them (and reloads the dataset on re-runs);
  manually it is `psql "$DATABASE_URL" -f etl/seed_app.sql` etc. from `backend/`.
- **Docker** applies all four automatically on the first `docker compose up`
  (they are mounted into Postgres's init directory).

## 3. Setup

### Option A — Local

**Step 1 — create the Postgres role and database** (one time):

```bash
sudo -u postgres psql -c "CREATE ROLE qorvexa LOGIN PASSWORD 'qorvexa' CREATEDB;"
sudo -u postgres createdb -O qorvexa qorvexa
```

**Step 2 — clone and run.** That is all — the script creates `backend/.env`
with a generated secret, builds the Python venv, loads the database, ingests
the corpus offline, installs frontend deps, and **starts both servers itself**:

```bash
git clone https://github.com/shubhambgp/mr-personal-assistant.git
cd mr-personal-assistant
export OPENAI_API_KEY=sk-...   # optional, but chat answers need it
./setup.sh
```

**Step 3 — open http://localhost:5173** and sign in (the script prints the
rep codes).

```bash
./setup.sh --stop     # stop both servers
./setup.sh --local    # restart (re-runs are fast — finished steps are skipped)
tail -f .dev/backend.log .dev/frontend.log   # server logs
```

If step 2 cannot reach Postgres it stops and prints the exact role/database
commands to run, derived from your own `DATABASE_URL`.

### Option B — Docker

Same two commands — with Docker installed and running, `./setup.sh` picks
Docker mode by itself:

```bash
git clone https://github.com/shubhambgp/mr-personal-assistant.git
cd mr-personal-assistant
export OPENAI_API_KEY=sk-...   # optional, but chat answers need it
./setup.sh
```

Open **http://localhost:8080**.

```bash
docker compose down       # stop (data survives)
docker compose down -v    # full reset (wipes DB + corpus)
```

## 4. Sign in

Any rep code from the dataset — e.g. **`7800001`** — with the shared demo
password **`qorvexa`**.

---

## Configuration

Two templates, because a `.env` can live in two places:

| Running | Copy | To |
|---|---|---|
| Docker | [`.env.example`](.env.example) | `.env` (repo root, beside `docker-compose.yml`) |
| Local | [`backend/.env.example`](backend/.env.example) | `backend/.env` |

Under Docker, `backend/.env` is deliberately **not** read (secrets stay out of
image layers) — compose passes the environment in from your shell or the root
`.env`. The root template lists exactly what is settable that way; the backend
template documents every setting. `setup.sh` fills the right one in for you —
but you can just as well do it by hand, or edit it any time later:

```bash
cp backend/.env.example backend/.env   # local mode (cp .env.example .env for Docker)
nano backend/.env                      # every value is explained in the file itself
./setup.sh --local                     # restart so the change takes effect
```

Only two values are required — everything else has a working default:

| Variable | Purpose |
|---|---|
| `JWT_SECRET` | Session signing key (≥32 chars) — generated by `setup.sh` |
| `OPENAI_API_KEY` | Chat + embeddings for newly uploaded documents |

Notable optional ones: `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/
`AGENDA_ENCRYPTION_KEY` (Gmail + Calendar — all three together or none) and
`COOKIE_SECURE` (must be `true` in production; the app refuses to start
otherwise). **Qdrant needs no credentials** (it runs in-process), **timezones
resolve themselves** (a connected Google account's zone, else the host's), and
**the frontend has no environment at all** — every API call is a same-origin
relative path by design.

## Connecting Gmail & Google Calendar (optional)

Needs one Google OAuth client **per deployment**, created by the operator —
reps never handle credentials, they just click *Connect* in Settings. Follow
[docs/GOOGLE_SETUP.md](docs/GOOGLE_SETUP.md) (~5 minutes). Without it the app
runs fine: tasks work, and Settings says exactly what is missing.

## What it does

| Area | Feature |
|---|---|
| **Ask your book** | Natural-language questions over doctors, visits, targets, RCPA, brand metrics — answered by scoped SQL tools that can only ever see *your* rows |
| **Grounded answers** | Numbers the model states are checked against tool results; unverified claims are flagged in the UI |
| **Literature (RAG)** | Hybrid semantic + lexical retrieval over approved SmPCs/detailing guides; per-rep private uploads (PDF/DOCX) in a dedicated Library page — files attached in any conversation land there too |
| **Agenda** | Gmail triage (who needs a reply, computed server-side), calendar, personal tasks |
| **Human-in-the-loop** | Every write to Google (send mail, create/move/cancel a meeting) pauses for the rep's approval, with a compliance verdict on the card; edits can change what is *said*, never who it is said *to* |
| **Compliance** | Deterministic rules (comparative claims, off-label, adverse-event routing, sample limits, PII) + an LLM reviewer; the final check re-runs on the exact bytes sent |

## URLs

Defined once in `frontend/src/lib/routes.ts`:

| Path | Page |
|---|---|
| `/login` | Sign in |
| `/personal-assistant` | Chat (new conversation) |
| `/personal-assistant/:conversationId` | A conversation — shareable, reload-safe |
| `/library` | Your documents + company literature |
| `/agenda` | Mail triage, calendar, tasks |
| `/settings` | Google connection |

## Testing

```bash
cd backend
.venv/bin/python -m pytest tests -q        # unit tests — no DB, ~3s
.venv/bin/python -m pytest evals -q        # guardrail evals — needs the loaded DB
.venv/bin/python -m evals.run_rag_eval     # retrieval quality — offline, no key
.venv/bin/ruff check .

cd frontend
npm run typecheck && npm run lint && npm run build
```

CI runs all of the above on every push.

## Project structure

```
backend/
  app/api/          HTTP layer only — auth, chat (SSE), conversations,
                    documents, agenda, health
  app/bot/          transport-agnostic agent core: LangGraph graph, prompts,
                    guardrails, compliance reviewer
  app/tools/        the agent's tools: scoped SQL, RAG, agenda + the registry
                    that mechanically rejects any identity/mailbox parameter
  app/services/     persistence & external clients (Postgres, Qdrant, Google)
  etl/              seed_app.sql (the dataset) + document ingestion
frontend/
  src/features/     feature-first: auth, chat, conversations, agenda, library,
                    settings
  src/lib/routes.ts every URL, defined once
  src/styles/       the design-token system (single source of truth)
```

## Security model (short version)

- **Identity comes from the verified JWT, only.** No request, header or tool
  argument can name a `chair_id`/`rep_id` — the tool registry rejects such a
  schema at startup, and tests assert it.
- **Everything the agent touches connects read-only**; chat history, graph
  checkpoints and Google credentials live in schemas that role cannot see.
- **Retrieval tenancy is exactly one filter** (`scope='global' OR chair_id=me`)
  that no caller can widen.
- **Writes to Google are gated by graph structure** — the approval node is the
  only path to the tool node, a reviewer runs first, and the service re-checks
  the final bytes.

Full details: [CLAUDE.md](CLAUDE.md) and
[docs/claude/security-invariants.md](docs/claude/security-invariants.md).

## More documentation

| Doc | What it is |
|---|---|
| [CLAUDE.md](CLAUDE.md) | The working agreement — the five rules that outrank everything else |
| [docs/claude/](docs/claude/) | Security invariants, architecture, conventions, development workflow |
| [ENGINEERING_LOG.md](ENGINEERING_LOG.md) | Numbered postmortems — why things are the way they are |
| [DATA.md](DATA.md) | The synthetic dataset |
| [docs/GOOGLE_SETUP.md](docs/GOOGLE_SETUP.md) | Creating the OAuth client for Gmail + Calendar |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | The CD half of the pipeline — images, ssh deploy, rollback |
| [docs/SENTRY_SETUP.md](docs/SENTRY_SETUP.md) | Error tracking: wired but off, and the redaction to do first |

## Before deploying anywhere real

Generate your own `JWT_SECRET`, use your own `OPENAI_API_KEY` and database
credentials, set `ENVIRONMENT=production` (which requires `COOKIE_SECURE=true`
and disables `/docs`), and put it behind TLS. The demo rep password is shared
and exists for the synthetic dataset only.

[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) has the rest: the CD workflow, the
five deploy-time traps specific to this app, and what the pipeline deliberately
does not do. Four things there are worth knowing before you start, because each
one is a decision rather than a setting:

* **One backend process, by design.** The rate limiters, the metrics and three
  caches are per-process, and local-mode Qdrant holds a file lock. Never add
  `--workers`; a second one silently multiplies every rate limit. Redis and a
  Qdrant server are what unlock horizontal scale.
* **A session token cannot be revoked** before its 8-hour expiry — logout clears
  the browser's copy, not the token. That is also why a connected mailbox is
  stored server-side instead of as a token claim: a stored connection is revoked
  in one statement.
* **Schema changes are manual.** There is no migration tool; the four `.sql`
  files are idempotent and applied by hand.
* **Error tracking is off.** Turning it on is two uncomments and a redactor —
  [docs/SENTRY_SETUP.md](docs/SENTRY_SETUP.md), and read the redaction section
  before the first event, because an event already sent cannot be unsent.
