# Connecting Gmail and Google Calendar

The agenda features — mail triage, drafting replies, the calendar, scheduling —
need a Google OAuth client. Without one the app still runs: tasks work, and
Settings says plainly that mail and calendar are unavailable.

**There is no per-user client ID.** One client identifies *the application* to
Google and is created once by whoever runs the deployment. Each rep then clicks
**Connect** in Settings and Google issues a **refresh token for that rep**, which
is encrypted and stored in `agenda.connections` under their `chair_id`. No rep
ever handles a secret, and nothing per-rep is ever configured by hand.

|                           | what it is                           | how many              | where it lives            |
| ------------------------- | ------------------------------------ | --------------------- | ------------------------- |
| Client ID + secret        | identifies the*app* to Google      | one per deployment    | the process environment   |
| `AGENDA_ENCRYPTION_KEY` | encrypts the stored tokens           | one per deployment    | the process environment   |
| Refresh token             | one rep's access to*their* mailbox | one per connected rep | the database, AES-256-GCM |

---

## Who is allowed to connect — decide this first

This is a setting in the Google Cloud console, not a code decision, and the four
options differ enormously in cost.

| Audience                                            | Who can connect                                                        | Google verification                                                                 | Refresh token life |
| --------------------------------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------ |
| External +**Testing**                         | only addresses you add by hand to the test-user list,**max 100** | none                                                                                | **7 days**   |
| **Internal** (Workspace / Cloud Identity org) | every user in your domain, unlimited                                   | **none**                                                                      | long-lived         |
| External +**Published**                       | any Google account                                                     | brand + app review**plus a CASA security assessment, redone every 12 months** | long-lived         |
| Workspace Marketplace / admin-trusted               | your domain, admin-installed                                           | none                                                                                | long-lived         |

Every scope that **reads** mail — `gmail.readonly`, `gmail.metadata`,
`gmail.modify` — is in Google's **restricted** tier, and that is what triggers
CASA. `gmail.send` and `calendar.events` are only **sensitive**: review, no CASA.

That is why a product like ChatGPT or Claude can accept any Gmail account and
this project cannot out of the box — they hold a verified restricted-scope
assessment and renew it annually.

**For a real field force, the answer is Internal.** The company already has a
Workspace domain, every rep is `@company.example`, and Internal apps skip
verification *and* the 100-user cap. That turns the requirement from "fund a
security audit" into "ask the Workspace admin". Follow the steps below and choose
**Internal** at step 3.

**For a demo or for development, use Testing.** Five minutes, free, and your own
Gmail works. Read the 7-day note at the bottom before you rely on it.

---

## Setup, about five minutes

The OAuth consent screen was reorganised into **Google Auth Platform** in 2025.
Older guides say `APIs & Services → OAuth consent screen`; that path no longer
matches the console.

**1. Project and APIs.** At [https://console.cloud.google.com/](https://console.cloud.google.com/) create a project
(or pick one). Under **APIs & Services → Library**, enable:

- **Gmail API**
- **Google Calendar API**

**2. Branding.** **Google Auth Platform → Branding**. App name, user support
email, developer contact. The app name is what a rep sees on the consent screen,
so make it recognisable.

**3. Audience.** **Google Auth Platform → Audience**.

- Choose **Internal** if you have a Workspace domain and only your own people
  will connect. Nothing else in this section applies to you — skip to step 4.
- Choose **External** otherwise. Leave **Publishing status** as **Testing**, then
  add every address that will connect under **Test users**. *In Testing mode that
  list is the entire access-control mechanism* — an address that is not on it
  cannot connect, and gets an error rather than a consent screen.

**4. Scopes.** **Google Auth Platform → Data Access → Add or remove scopes**, then
paste these five. They are exactly what
[`backend/app/integrations/google/oauth.py`](../backend/app/integrations/google/oauth.py)
requests:

```
openid
email
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/calendar.events
https://www.googleapis.com/auth/gmail.readonly
```

The last one depends on `AGENDA_GMAIL_SCOPE`:

- `readonly` (default) reads message bodies. Needed to read a thread, to draft a
  grounded reply, and for `search_mail`.
- `metadata` sees only headers, labels and thread structure — no bodies. The
  whole triage view still works, because the categories are computed from *who
  sent the last message and when*. Use `https://www.googleapis.com/auth/gmail.metadata`
  in place of `gmail.readonly` above.

  **`search_mail` is not offered at all under `metadata`**, because Google rejects
  the search query parameter for that scope. The app withholds the tool rather
  than offering one that fails.

Deliberately **not** requested: `gmail.modify` (nothing here marks mail read,
labels or archives, so a stolen token cannot alter the mailbox) and
`gmail.compose` (drafts live in the approval card, not in Gmail).

**5. Create the client.** **Google Auth Platform → Clients → Create client**.

- Application type: **Web application**
- Under **Authorised redirect URIs**, add exactly:

  ```
  http://localhost:8000/api/agenda/callback
  ```

  It must match `GOOGLE_REDIRECT_URI` character for character — Google rejects a
  redirect URI that was not registered. Plain `http` is correct here, not a
  shortcut: Google permits `http://localhost` specifically.

**6. Configure the app.** Copy the client ID and secret into `backend/.env`:

```bash
GOOGLE_CLIENT_ID=<from the console>
GOOGLE_CLIENT_SECRET=<from the console>
GOOGLE_REDIRECT_URI=http://localhost:8000/api/agenda/callback

# base64url of exactly 32 random bytes:
#   python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
AGENDA_ENCRYPTION_KEY=<generate one>
```

All three of the first values are required together — the app refuses to start
half-configured rather than failing later at the first mail request.

Restart the backend, open the app, and click **Settings → Connect**.

---

## Things that surprise people

**"Google hasn't verified this app".** In Testing mode every consent screen shows
this. Click **Advanced → Go to … (unsafe)** to continue. It is expected, not a
misconfiguration — the app is unverified because you have not submitted it for
verification, which you do not need to do for your own use.

**Authorisation expires after 7 days in Testing mode.** This is Google's rule for
External + Testing, not a bug here, and it is the single most likely thing to
confuse you a week after setup.

The app handles it honestly: when the grant dies, the stored credential is
**deleted**, the connection is marked stale, Settings shows an amber
**Reconnect** for that address, and the assistant says the connection expired
rather than reporting a mailbox error. Click **Reconnect**. The same handling
covers a rep revoking access at [https://myaccount.google.com/permissions](https://myaccount.google.com/permissions) or
changing their password. **Internal audience does not expire this way.**

**What a leaked client secret does and does not buy.** On its own, nothing: it
cannot read any mailbox. The per-rep **refresh tokens** are the credentials that
can, they live in the database, and they are encrypted under a *different* secret
(`AGENDA_ENCRYPTION_KEY`). That separation is the point — one leak is not enough.
Rotate the client secret in the console; rotate the encryption key and every
stored token becomes unreadable, which is why `key_version` is on the row.

---

## Production

- Inject all four values as **environment variables** — the container's
  environment, Compose `secrets`, or the platform's secret store. Never a `.env`
  baked into an image. `.env` is gitignored; the `.env.example` templates are the only committed
  one and holds no values.
- `GOOGLE_REDIRECT_URI` becomes your real **HTTPS** callback, and it must be
  registered in the console first or Google refuses the flow. This fails at deploy
  time, so check it before you ship.
- Set `COOKIE_SECURE=true`.
- Prefer the **Internal** audience. If you genuinely need any Google account to be
  able to connect, budget for the CASA assessment and its annual recertification
  before promising the feature.
- The refresh tokens in `agenda.connections` are the most sensitive rows in the
  database. They are encrypted at rest, live in their own schema, and the
  read-only role the SQL tools connect as has no privilege there — none of which
  should be relaxed. See the header comment in
  [`backend/etl/agenda_schema.sql`](../backend/etl/agenda_schema.sql).

## Disconnecting

**Settings → Disconnect** revokes the grant at Google and **deletes the row**, so
the credential is gone from the database and gone at Google.

Two things deliberately survive: the rep's own **tasks**, which mostly have
nothing to do with Gmail, and **`agenda.outbound_log`**, the record of what was
sent to prescribers and who approved it — the artefact that makes the approval
gate worth having.
