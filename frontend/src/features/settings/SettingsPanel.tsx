// Settings: where a rep connects their own Google account.
//
// The rep never handles a secret. This deployment has ONE Google OAuth client,
// configured by the operator as an environment variable; clicking Connect sends
// the rep to Google's consent screen, and what comes back is a refresh token that
// is encrypted and stored server-side under their own row. Disconnect revokes it
// at Google and deletes the row.

import { Check, Mail, Plug, ShieldCheck, TriangleAlert, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Badge, Button, Card, Spinner } from '@/components/ui'
import { api, ApiError } from '@/lib/api'
import { CONTENT_COL } from '@/lib/format'
import { cx } from '@/lib/cx'
import type { GoogleConnection } from '@/lib/types'

/** Scope -> what it actually lets the app do, in the rep's own terms.
 *  Shown because "grant access to your email" is not consent if nobody says
 *  which access. */
const SCOPE_PLAIN: Record<string, string> = {
  'gmail.readonly': 'Read your mail',
  'gmail.metadata': 'Read who your mail is from and when (not the contents)',
  'gmail.send': 'Send mail as you — only after you approve each one',
  'calendar.events': 'Read your calendar and add events you approve',
  email: 'See which account is connected',
  openid: 'See which account is connected',
}

function plainScope(scope: string): string | null {
  const tail = scope.split('/').pop() ?? scope
  return SCOPE_PLAIN[tail] ?? null
}

export function SettingsPanel({ active }: { active: boolean }) {
  const [connection, setConnection] = useState<GoogleConnection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!active) return
    let cancelled = false
    void (async () => {
      try {
        const data = await api.googleConnection()
        if (!cancelled) {
          setConnection(data)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not read your settings.')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [active])

  const disconnect = useCallback(async () => {
    setBusy(true)
    try {
      await api.disconnectGoogle()
      setConnection((prev) =>
        prev ? { ...prev, connected: false, email_account: null, scopes: [] } : prev,
      )
      setConfirming(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not disconnect.')
    } finally {
      setBusy(false)
    }
  }, [])

  return (
    <div className="scrollbar-thin min-h-0 min-w-0 overflow-y-auto px-4 py-5 sm:px-6">
      <div className={cx(CONTENT_COL, 'flex flex-col gap-5')}>
        <header>
          <h2 className="font-serif text-xl text-fg">Settings</h2>
          <p className="text-2xs text-fg-subtle">Connect the accounts this assistant may use.</p>
        </header>

        {error && (
          <p role="alert" className="rounded-card bg-danger/12 px-3 py-2 text-2xs text-danger">{error}</p>
        )}

        {!connection ? (
          /* No spinner while an error is showing: an eternal spinner under an
             error banner promises progress that is not coming. */
          error ? null : <Spinner />
        ) : (
          <Card className="flex flex-col gap-3 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Mail className="size-4 text-fg-muted" aria-hidden="true" />
              <h2 className="text-sm font-medium text-fg">Gmail and Google Calendar</h2>
              {connection.connected ? (
                <Badge tone="success">
                  <Check className="size-3" aria-hidden="true" />
                  connected
                </Badge>
              ) : connection.stale ? (
                /* Amber, not green and not grey. The whole point of the stale
                   state is that this used to read "connected" over a mailbox
                   that answered 400 to everything. */
                <Badge tone="warning">
                  <TriangleAlert className="size-3" aria-hidden="true" />
                  expired
                </Badge>
              ) : (
                <Badge tone="neutral">not connected</Badge>
              )}
            </div>

            {!connection.configured ? (
              <p className="text-2xs text-fg-muted">{connection.why}</p>
            ) : connection.connected ? (
              <>
                <p className="text-xs text-fg-muted">
                  Connected as <span className="font-medium text-fg">{connection.email_account}</span>
                  {connection.calendar_tz ? ` · calendar timezone ${connection.calendar_tz}` : ''}
                </p>

                {connection.scopes.length > 0 && (
                  <ul className="flex flex-col gap-1">
                    {connection.scopes.map((scope) => {
                      const plain = plainScope(scope)
                      return plain ? (
                        <li key={scope} className="flex items-start gap-1.5 text-2xs text-fg-muted">
                          <ShieldCheck
                            className="mt-0.5 size-3 shrink-0 text-success"
                            aria-hidden="true"
                          />
                          {plain}
                        </li>
                      ) : null
                    })}
                  </ul>
                )}

                {confirming ? (
                  /* The in-place danger swap, as elsewhere. Disconnecting
                     destroys a stored credential, so it gets a confirmation. */
                  <div className="flex items-center gap-1 rounded-lg bg-danger/12 px-2 py-1">
                    <span className="min-w-0 flex-1 text-2xs text-danger">
                      Disconnect and delete the stored token?
                    </span>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => void disconnect()}
                      disabled={busy}
                    >
                      {busy ? <Spinner /> : <Check className="size-3.5" aria-hidden="true" />}
                      Disconnect
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
                      <X className="size-3.5" aria-hidden="true" />
                      Keep
                    </Button>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="md" onClick={() => setConfirming(true)}>
                      Disconnect
                    </Button>
                  </div>
                )}
                <p className="text-2xs text-fg-subtle">
                  Disconnecting revokes access at Google and deletes the stored token. Your tasks and
                  the record of what you have already approved and sent are kept.
                </p>
              </>
            ) : (
              <>
                {connection.stale ? (
                  <>
                    <p className="text-xs text-fg-muted">
                      The connection to{' '}
                      <span className="font-medium text-fg">{connection.email_account}</span> has
                      expired, so mail and calendar are unavailable until you reconnect. Your stored
                      credential has already been deleted.
                    </p>
                    <p className="text-2xs text-fg-muted">{connection.why}</p>
                  </>
                ) : (
                  <p className="text-xs text-fg-muted">
                    Connect your account to see which mail needs a reply, what is on your calendar,
                    and to draft replies here. Nothing is ever sent, and no meeting is ever created,
                    without you approving it first.
                  </p>
                )}
                <div>
                  {/* A real navigation, not fetch: the server redirects to
                      Google's consent screen, and an XHR cannot follow that. */}
                  <Button
                    variant="primary"
                    size="lg"
                    onClick={() => {
                      window.location.href = api.connectGoogleUrl()
                    }}
                  >
                    <Plug className="size-4" aria-hidden="true" />
                    {connection.stale ? 'Reconnect Google' : 'Connect Google'}
                  </Button>
                </div>
                <p className="text-2xs text-fg-subtle">
                  {connection.stale
                    ? 'Reconnecting asks Google for consent again and replaces the stored credential. Your tasks are untouched.'
                    : 'You will be asked to grant read access to your mail, permission to send mail as you, and access to your calendar events.'}
                </p>
              </>
            )}
          </Card>
        )}
      </div>
    </div>
  )
}
