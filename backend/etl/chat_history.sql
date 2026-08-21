-- Chat history. Lives in `public`, NOT in the `app` schema.
--
-- That separation is deliberate: etl/load_postgres.py drops and recreates the
-- `app` schema on every data reload. Conversations must survive a reload, so
-- they live somewhere the loader never touches.
--
-- Every row carries chair_id, and every read filters on the chair_id from the
-- verified JWT — the same discipline as the tool layer. A conversation is
-- rep-owned data, not shared.
--
-- Apply with:  psql "$DATABASE_URL" -f etl/chat_history.sql

CREATE TABLE IF NOT EXISTS public.conversations (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chair_id             bigint      NOT NULL,
    title                text,
    -- OpenAI's server-side conversation handle. We store one id per
    -- conversation rather than mirroring the message history.
    previous_response_id text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- The sidebar query: this rep's conversations, most recent first.
CREATE INDEX IF NOT EXISTS idx_conversations_chair_updated
    ON public.conversations (chair_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.messages (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id   uuid        NOT NULL
                      REFERENCES public.conversations (id) ON DELETE CASCADE,
    role              text        NOT NULL CHECK (role IN ('user', 'assistant')),
    content           text        NOT NULL DEFAULT '',
    -- The tool-call timeline, as rendered in the UI: name, args, result,
    -- duration, error flag. Stored so a resumed conversation shows the same
    -- intermediate steps the rep originally saw, not just the final text.
    tool_calls        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    grounded          boolean,
    unverified_claims jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- Set when this turn PAUSED for a human approval, and NULLed when it
    -- resumes. Holds the approval_required payload: the pending call, its
    -- editable fields, and the compliance verdict the rep was shown.
    --
    -- NOT merely a convenience for the UI. The interrupt itself lives in the
    -- graph checkpoint (schema `agent`), and the frontend rebuilds a
    -- conversation from this table — so with nothing here, a rep who reloads
    -- loses the card, and their next message re-enters a thread whose
    -- interrupted task is still pending, which interrupts again immediately.
    -- The conversation would be permanently wedged.
    --
    -- The checkpoint stays the authority: /api/chat/resume matches the
    -- interrupt_id against the live graph state and refuses on a mismatch, so a
    -- stale row or a second browser tab cannot approve a superseded draft.
    pending_approval  jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Idempotent for an existing database: the table above is CREATE IF NOT EXISTS,
-- so a column added later needs its own statement.
ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS pending_approval jsonb;

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON public.messages (conversation_id, created_at);
