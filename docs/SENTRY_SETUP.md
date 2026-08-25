# Sentry — how to turn it on, and the one thing to get right first

Error tracking is **wired but off**. `app/main.py` initialises Sentry only when
`SENTRY_DSN` is set, and if the package is missing it logs a warning and carries
on:

```python
if settings.sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            send_default_pii=False,         # live already — see below
            include_local_variables=False,  # live already — see below
            # before_send=_before_send,     # uncomment with the function above it
            # traces_sample_rate=0.05,
        )
    except ImportError:
        log.warning("SENTRY_DSN set but sentry-sdk is not installed")
```

`sentry-sdk` is commented out in `requirements.txt` on purpose, so the base image
carries no telemetry dependency and a fresh checkout sends nothing anywhere.
There is nothing to undo — this is a hook waiting for a deployment.

---

## Turning it on — two uncomments

Nothing has to be written. Both pieces already sit in the tree, commented, with
their reasons next to them:

| Where | What is commented | Why it is not live |
|---|---|---|
| `backend/requirements.txt` | `# sentry-sdk==2.20.0` | so the base image carries no telemetry dependency |
| `backend/app/main.py` | the `_before_send` redactor, and the two `init` lines that use it | there is nothing to send events to yet |

1. **Uncomment the pin** in `requirements.txt`, then `pip install -r`.
2. **Uncomment `_before_send`** in `app/main.py` — the function *and* the
   `before_send=` / `traces_sample_rate=` lines inside `sentry_sdk.init`.
3. **Set `SENTRY_DSN`** where the app's other secrets live: `backend/.env`
   locally, the server's `.env` under Docker (compose forwards it already).
4. **Restart.** The startup line `sentry enabled` is the confirmation.

`environment` is passed through already, so events land tagged `development`,
`docker` or `production` and you can filter on it.

---

## Why step 2 is not optional

This is the part worth reading twice.

Sentry captures **local variables from the stack frames** of an exception, and
`include_local_variables` defaults to **True**. In this app those frames hold
`body`, `subject`, `thread_text`, doctor names, email addresses and mobile
numbers. An error inside `services/agenda.send_mail` would ship a draft email
addressed to a prescriber straight to a third-party SaaS — precisely what
`AuditLogger.log` exists to prevent (CLAUDE.md §1.10: *never log a mail body*).

So two of the settings in `sentry_sdk.init` are **already live**, even with the
DSN empty, because they are safety rather than tuning:

```python
send_default_pii=False,        # cookies and the request body stay out —
                               # the session cookie IS the auth token
include_local_variables=False, # drafts stay out; the default is wrong here
```

And the commented redactor catches whatever those two miss. It reuses
`app/bot/audit.py:scrub` — the audit log's own redactor, made public for exactly
this second caller — rather than carrying a second idea of what PII is (§1.4:
PII is defined in one place, and the copy that drifts is the one that leaks):

```python
def _before_send(event, hint):
    event = scrub(event)                                    # same regexes as the audit log
    event.setdefault("tags", {})["request_id"] = request_id_var.get()
    chair = chair_id_var.get()
    if chair is not None:
        event["tags"]["chair_id"] = str(chair)              # an integer, never rep_name
    return event
```

`request_id` is the same id the API returns in `X-Request-ID`, so a rep who
quotes it can be matched to an event without anyone searching by name.

---

## The frontend

There is no Sentry in `frontend/` today. `app/ErrorBoundary.tsx` catches render
errors and writes them to `console.error`, which nobody reads in production.

If you add `@sentry/react`, two notes specific to this codebase:

* A DSN is public by design, so a `VITE_SENTRY_DSN` baked into the bundle is
  fine — this is the one exception to "no `VITE_*` values" in
  `docs/claude/frontend-practices.md` §9, and worth a comment saying so.
* Send from the existing `ErrorBoundary`'s `componentDidCatch`, not a global
  handler. The boundary already knows which subtree failed, and the app has two
  of them — one around the shell and one around `MessageList` — so the event can
  say which.

---

## Checking it works

Add a throwaway route, trigger it once, delete it:

```python
@router.get("/boom")           # DELETE THIS AFTER ONE CALL
def boom():
    raise RuntimeError("sentry smoke test")
```

Then look at the event in Sentry and confirm three things:

- the `request_id` tag is there and matches the `X-Request-ID` header
- there is **no** `Cookie` header and **no** local variables in the frames
- the `chair_id` tag is a number, and `rep_name` appears nowhere

If any of those is wrong, fix it before real traffic — an event already sent
cannot be unsent.

---

## Why it is off today

Sentry earns its place when something is deployed and nobody is watching the
logs. This runs on a laptop, single process, with structured JSON logs on stdout
and `/api/metrics` for counters — so today it would add a dependency, a third
party and a PII surface, in exchange for nothing.

The hook stays because turning it on later should be uncommenting a pin, not
reading the codebase to work out where `init` belongs.
