-- LangGraph checkpoint storage. Lives in its own `agent` schema, NOT in `app`
-- and NOT in `public`.
--
-- THIS IS NOT TIDINESS. AsyncPostgresSaver has no schema parameter: its
-- .setup() runs `CREATE TABLE IF NOT EXISTS checkpoints / checkpoint_blobs /
-- checkpoint_writes / checkpoint_migrations` **unqualified**, so the tables land
-- wherever the connection's search_path resolves. Our DATABASE_URL carries
-- `options=-csearch_path%3Dapp,public`, so the default would be `app` — and that
-- causes two serious bugs at once:
--
--   1. STATE DESTROYED ON EVERY RELOAD. etl/load_postgres.py does
--      `DROP SCHEMA app CASCADE` and renames a freshly built schema over it.
--      Every rep's conversation history would vanish on each data load.
--
--   2. A CROSS-REP HISTORY LEAK. `ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT
--      SELECT ON TABLES TO qorvexa_ro` (also in load_postgres.py) means any new
--      table created there is automatically readable by the read-only role. The
--      checkpoint tables hold full message content, and run_sql's denylist is
--      built from the manifest — which would not know these names. So
--      `SELECT * FROM checkpoints` would have passed every guard and returned
--      other reps' transcripts.
--
-- See ENGINEERING_LOG entry 15. Same class of bug as entry 6 (chat history),
-- and the fix is the same shape: put durable state somewhere the read-only role
-- has no grants, so run_sql cannot reach it *structurally* rather than being
-- stopped by a pattern.
--
-- The checkpointer therefore opens its own connection with search_path=agent.
-- No GRANT to qorvexa_ro appears in this file, and none should ever be added.
--
-- Apply with:  psql "$DATABASE_URL" -f etl/agent_schema.sql
--              then app startup calls AsyncPostgresSaver.setup() to create the
--              tables inside it.

CREATE SCHEMA IF NOT EXISTS agent;

-- Explicit and deliberate: revoke rather than rely on the absence of a grant,
-- so a future blanket `GRANT ... ON ALL SCHEMAS` cannot widen this by accident.
REVOKE ALL ON SCHEMA agent FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qorvexa_ro') THEN
        REVOKE ALL ON SCHEMA agent FROM qorvexa_ro;
        REVOKE ALL ON ALL TABLES IN SCHEMA agent FROM qorvexa_ro;
        -- And for tables that do not exist yet: .setup() creates them later.
        ALTER DEFAULT PRIVILEGES IN SCHEMA agent REVOKE ALL ON TABLES FROM qorvexa_ro;
    END IF;
END
$$;
