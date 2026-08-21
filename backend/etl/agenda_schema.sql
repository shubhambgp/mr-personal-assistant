-- Google connections, the rep's own tasks, and the outbound compliance log.
-- Lives in its own `agenda` schema, NOT in `app` and NOT in `public`.
--
-- THIS IS NOT TIDINESS, and it is the third time this exact reasoning has been
-- needed here (chat history: ENGINEERING_LOG 6; graph checkpoints: 15). `app`
-- would have been wrong twice over:
--
--   1. STATE DESTROYED ON EVERY RELOAD. etl/load_postgres.py does
--      `DROP SCHEMA app CASCADE`. Every rep's Google connection — and therefore
--      their consent — would vanish on each data load, and it would look like
--      Google's fault. (The tempting shortcut is a column on `reps`, following
--      the `password_hash` precedent. Same bug.)
--
--   2. A CREDENTIAL REACHABLE BY THE MODEL. `ALTER DEFAULT PRIVILEGES IN SCHEMA
--      app GRANT SELECT ON TABLES TO qorvexa_ro` auto-grants every new table
--      there to the role run_sql connects as, and run_sql's denylist is built
--      from the manifest, which would not know this table's name. A refresh
--      token is a long-lived credential to a real person's mailbox. Encrypted
--      or not, it does not belong anywhere model-composed SQL can reach.
--
-- `public` was also rejected, for a weaker but sufficient reason: qorvexa_ro has
-- no grants there today, and that is the only thing protecting chat history.
-- Stacking a mailbox credential onto the same single un-granted schema means one
-- careless `GRANT ... ON SCHEMA public` leaks transcripts AND credentials.
-- Separate blast radii.
--
-- Apply with:  psql "$DATABASE_URL" -f etl/agenda_schema.sql

CREATE SCHEMA IF NOT EXISTS agenda;

-- Explicit and deliberate: revoke rather than rely on the absence of a grant, so
-- a future blanket `GRANT ... ON ALL SCHEMAS` cannot widen this by accident.
REVOKE ALL ON SCHEMA agenda FROM PUBLIC;


-- One connected Google account per rep.
CREATE TABLE IF NOT EXISTS agenda.connections (
    -- chair_id is the PRIMARY KEY because it is the tenancy axis everywhere
    -- else in this system: public.conversations, the Qdrant payload filter,
    -- every scoped CTE, RepContext.cache_key(). A second identity axis is
    -- exactly what CLAUDE.md §1.1 exists to prevent.
    chair_id            bigint      PRIMARY KEY,

    -- rep_code is stored AND verified alongside chair_id on every use.
    --
    -- Measured: the dataset has 25 reps, 1:1 between chair_id and rep_code, no
    -- chair with two codes and no code across two chairs — so either would work
    -- as the key today. rep_code is carried because a field force REASSIGNS a
    -- rep code when someone leaves. Keyed on one identifier alone, the
    -- replacement would silently inherit the previous rep's Gmail token. So
    -- services/agenda.connection() asserts BOTH match the verified JWT, and a
    -- mismatch deletes the row rather than serving it.
    rep_code            bigint      NOT NULL,

    -- From Google's userinfo at connect time. The ONLY source of
    -- ctx.email_account; see CLAUDE.md §1.7.
    email_account       text        NOT NULL,

    -- Recorded as GRANTED, not as requested: Google may return fewer scopes
    -- than were asked for, and a tool must degrade on what was actually given.
    scopes              text[]      NOT NULL,

    -- From the calendar settings at connect time. Needed because every event
    -- write requires an IANA zone, and guessing one books the wrong hour.
    calendar_tz         text,

    -- AES-256-GCM: base64url(nonce || ciphertext || tag). The key is
    -- AGENDA_ENCRYPTION_KEY, read in app/config.py and nowhere else.
    --
    -- NULLABLE, which it did not used to be. Google's grant can die while the
    -- row lives: in External+Testing audience a refresh token expires after
    -- SEVEN DAYS, and in any audience the rep can revoke access from their
    -- Google account page or change their password. When that happens the
    -- credential is deleted and needs_reconnect_at is stamped, because a dead
    -- refresh token is still the most dangerous artefact in this database and
    -- keeping it buys nothing — it cannot authenticate anything.
    refresh_token_enc   text,
    -- So a key rotation can re-wrap rows without guessing which key made them.
    key_version         int         NOT NULL DEFAULT 1,

    -- Set when the grant is known dead. The row survives so Settings can say
    -- WHICH account to reconnect; before this existed an expired connection
    -- reported itself as connected forever and every mail tool returned a bare
    -- "Google returned 400."
    needs_reconnect_at  timestamptz,

    connected_at        timestamptz NOT NULL DEFAULT now(),
    last_refreshed_at   timestamptz,

    -- Two states, never a half-state. A live row HAS a credential and no
    -- reconnect stamp; a stale row has the stamp and no credential.
    CONSTRAINT connections_credential_state
        CHECK ((refresh_token_enc IS NULL) = (needs_reconnect_at IS NOT NULL))
);


-- The rep's own agenda: tasks they add by hand, and tasks the assistant creates
-- when they say "remind me to send Dr Sharma the dosing card on Friday".
--
-- NOT gated by the approval interrupt. A private to-do is not a regulated
-- action, and gating it would train the rep to click through approvals —
-- weakening the gate that actually matters.
CREATE TABLE IF NOT EXISTS agenda.tasks (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    chair_id    bigint      NOT NULL,
    title       text        NOT NULL,
    notes       text,
    due_date    date,

    -- Nullable, and separate from due_date rather than one timestamptz: "Friday"
    -- and "Friday 4 pm" are genuinely different states, and a timestamp forces
    -- you to invent the missing half. Tasks also work with no Google connection,
    -- so calendar_tz may not exist — guessing a zone would show the wrong hour.
    -- integrations/google/calendar.py's _when() draws the same all-day/timed
    -- distinction for events.
    due_time    time,

    -- A flag, not a 1-5 priority. Sorts to the top of whichever section the task
    -- already belongs to; it is deliberately NOT a section of its own, because a
    -- task that is both important and overdue is genuinely both, and one row in
    -- two places makes every count a half-truth.
    important   boolean     NOT NULL DEFAULT false,

    -- Set when schedule_task puts this task on the rep's Google Calendar, so the
    -- panel can show it as scheduled and not book it twice. Text, because it is
    -- Google's opaque id and means nothing to us.
    calendar_event_id text,

    -- Plain bigint, NO foreign key, on purpose: it references app.doctors, and
    -- `DROP SCHEMA app CASCADE` drops a cross-schema constraint silently rather
    -- than refusing. A dangling id is tolerable; a constraint that disappears
    -- without an error is not.
    doctor_id   bigint,

    -- Who created it. Worth distinguishing: "the assistant thought I promised
    -- this" and "I wrote this down" are different kinds of claim.
    source      text        NOT NULL DEFAULT 'rep' CHECK (source IN ('rep', 'assistant')),

    done_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT tasks_time_needs_date
        CHECK (due_time IS NULL OR due_date IS NOT NULL)   -- a time alone means nothing
);

-- What was sent to a prescriber, and the evidence a human approved it.
--
-- Gmail's Sent folder records that a mail went out. It cannot record that a
-- compliance verdict was computed, shown to a rep, and approved by them. That
-- record is the entire defensibility claim of this feature, so it lives here.
--
-- Append-only by construction: nothing in the application ever UPDATEs or
-- DELETEs a row. An audit row that can be edited is not an audit row.
CREATE TABLE IF NOT EXISTS agenda.outbound_log (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    chair_id            bigint      NOT NULL,
    -- No FK to public.conversations: the two schemas are applied by separate
    -- scripts and either can be reset independently. A dangling id here is fine;
    -- a migration that fails on a cross-schema FK is not.
    conversation_id     uuid,
    kind                text        NOT NULL CHECK (kind IN ('email', 'calendar_event')),
    status              text        NOT NULL CHECK (status IN ('sent', 'failed', 'rejected')),

    -- Recipients as actually sent. "Who did we tell what" is the first question
    -- a compliance audit asks.
    recipients          text[]      NOT NULL DEFAULT '{}',
    subject             text,
    -- The regulated artefact, verbatim. Stored HERE and deliberately not in
    -- logs/audit.jsonl, which gets shipped to aggregators.
    body                text,

    doctor_id           bigint,
    thread_id           text,
    provider_message_id text,

    -- The verdict the rep was shown, as they were shown it.
    compliance          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- Whether the rep changed the wording before approving. The pre-edit draft
    -- is in the audit log; this makes divergence findable without joining.
    edited_by_rep       boolean     NOT NULL DEFAULT false,
    approved_at         timestamptz NOT NULL DEFAULT now(),
    error               text
);

CREATE INDEX IF NOT EXISTS idx_agenda_outbound_chair_time
    ON agenda.outbound_log (chair_id, approved_at DESC);


-- ---------------------------------------------------------------------------
-- In-place upgrades for databases created before the columns above existed.
--
-- Every CREATE above is IF NOT EXISTS, which means an existing table is left
-- exactly as it was — so a new column defined in the CREATE reaches a fresh
-- install and NOT a running one. These ALTERs close that gap and are no-ops on
-- a fresh database, which is what keeps `psql -f` re-runnable (CI applies this
-- file twice).
-- ---------------------------------------------------------------------------

ALTER TABLE agenda.connections ALTER COLUMN refresh_token_enc DROP NOT NULL;
ALTER TABLE agenda.connections ADD COLUMN IF NOT EXISTS needs_reconnect_at timestamptz;

ALTER TABLE agenda.tasks ADD COLUMN IF NOT EXISTS due_time          time;
ALTER TABLE agenda.tasks ADD COLUMN IF NOT EXISTS important         boolean NOT NULL DEFAULT false;
ALTER TABLE agenda.tasks ADD COLUMN IF NOT EXISTS calendar_event_id text;

-- Postgres has no ADD CONSTRAINT IF NOT EXISTS, so drop-then-add is what makes
-- this file re-runnable. Both are cheap: these tables are small.
ALTER TABLE agenda.connections DROP CONSTRAINT IF EXISTS connections_credential_state;
ALTER TABLE agenda.connections ADD  CONSTRAINT connections_credential_state
    CHECK ((refresh_token_enc IS NULL) = (needs_reconnect_at IS NOT NULL));

ALTER TABLE agenda.tasks DROP CONSTRAINT IF EXISTS tasks_time_needs_date;
ALTER TABLE agenda.tasks ADD  CONSTRAINT tasks_time_needs_date
    CHECK (due_time IS NULL OR due_date IS NOT NULL);

-- Indexes come AFTER the ALTERs, and that order is load-bearing. Written the
-- other way round, the first run against an existing database DROPPED the index
-- and then failed to recreate it, because due_time did not exist yet — leaving
-- the table unindexed until someone happened to run the file a second time.
--
-- One index, extended to cover the ordering the section split needs. No second
-- index on `important`: this table holds tens of rows per rep, and an index for
-- imagined scale is cargo cult.
DROP INDEX IF EXISTS agenda.idx_agenda_tasks_chair;
CREATE INDEX IF NOT EXISTS idx_agenda_tasks_chair
    ON agenda.tasks (chair_id, done_at, due_date, due_time);


-- The read-only role gets NOTHING here, and none of this may be relaxed. These
-- three tables hold a mailbox credential and every word a rep has sent a doctor;
-- run_sql connects as qorvexa_ro, and the only thing keeping it out is the
-- absence of a privilege. Do not add a GRANT.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qorvexa_ro') THEN
        REVOKE ALL ON SCHEMA agenda FROM qorvexa_ro;
        REVOKE ALL ON ALL TABLES IN SCHEMA agenda FROM qorvexa_ro;
        ALTER DEFAULT PRIVILEGES IN SCHEMA agenda REVOKE ALL ON TABLES FROM qorvexa_ro;
    END IF;
END
$$;
