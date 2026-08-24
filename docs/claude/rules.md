# Working rules for AI agents in this repo

* Read `docs/claude/security-invariants.md` before changing anything. It is a
  list of invariants that a plausible-looking change can silently break — not
  style guidance.
* If you touch `app/tools/`, `app/core/security.py`, `app/deps.py` or
  `app/services/vectors.py`, re-read the security invariants before you finish.
  Those files are where a subtle change becomes a data leak.
* Run the tests. `backend/tests/` needs no database and takes about a second.
* Do not add a dependency to work around something the standard library does.
* When a guard rejects something the model wanted to do, the fix is usually to
  give the model a better tool — not to loosen the guard.
* Prefer failing loudly at load/startup over degrading quietly at runtime.
* **Comments** explain *why*, not *what*. If a line looks wrong but is right,
  say why it is right — several comments in this repo mark exactly that. Do not
  delete such a comment without understanding what it protects.
* Never commit: `.env`, `client_secret*.json`, `token.json`, `audit.jsonl`,
  `*.duckdb`, anything from `data/literature.local/` or a real mailbox. The
  confidentiality CI job scans for vendor identifiers by hash — do not name the
  vendor anywhere, including in test data and comments.
* `ENGINEERING_LOG.md` records why things are the way they are, numbered.
  When an invariant references "ENGINEERING_LOG N", that entry is the full
  story. Add an entry when you fix something whose cause was non-obvious.
