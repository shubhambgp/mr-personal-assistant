import { useState } from 'react'
import type { FormEvent, InputHTMLAttributes } from 'react'

import { Button, Card, Spinner } from '@/components/ui'
import { ApiError } from '@/lib/api'

import { useAuth } from './authContext'

export function LoginPage() {
  const { login } = useAuth()
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(identifier.trim(), password)
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError(`Too many attempts. Try again in ${err.retryAfter ?? 60}s.`)
      } else if (err instanceof ApiError && err.status === 401) {
        // The server does not distinguish wrong-password from unknown-rep, and
        // neither does this message — that is deliberate.
        setError('Invalid credentials.')
      } else {
        // A network failure or a 5xx is NOT a credentials problem, and telling
        // an offline rep their password is wrong sends them to reset a password
        // that was never wrong.
        setError('Could not reach the server. Check your connection and try again.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    /* Two columns at md+: a brand panel that says what this is, and the form.
       Below md the brand panel is simply hidden, so the phone experience is the
       single centered card it always was. Tokens only — sunken and accent-soft
       both invert with the theme, so no dark: variants are needed. */
    <div className="grid min-h-dvh md:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden overflow-hidden bg-sunken md:flex md:flex-col md:justify-between md:p-10">
        {/* Decorative discs, not information: aria-hidden and behind the text. */}
        <div
          aria-hidden="true"
          className="absolute -right-24 -top-24 size-96 rounded-full bg-accent-soft"
        />
        <div
          aria-hidden="true"
          className="absolute -bottom-32 -left-16 size-80 rounded-full bg-accent-soft opacity-60"
        />

        <p className="relative text-label uppercase text-fg-subtle">Qorvexa</p>

        <div className="relative">
          {/* A styled p, not a heading: this panel is display:none below md, so
              making it the h1 would leave the phone page with no h1 at all. The
              card's title below is the real page heading on every viewport. */}
          <p className="font-serif text-2xl leading-snug text-fg">
            Your territory,
            <br />
            ready to answer.
          </p>
          <p className="mt-3 max-w-sm text-sm leading-relaxed text-fg-muted">
            Doctors, visits, literature and your agenda — one assistant, grounded in your own data,
            with nothing sent to a prescriber unless you approve it.
          </p>
        </div>

        <p className="relative text-2xs text-fg-subtle">MR Personal Assistant</p>
      </div>

      <div className="flex items-center justify-center px-4 py-10">
        <Card className="animate-rise w-full max-w-sm p-6">
          <div className="mb-6 text-center">
            <h1 className="font-serif text-xl text-fg">MR Personal Assistant</h1>
            <p className="mt-1 text-2xs text-fg-subtle">Sign in with your rep code</p>
          </div>

          <form onSubmit={submit} className="space-y-3">
            <Field
              id="identifier"
              label="Rep code or chair ID"
              value={identifier}
              onChange={setIdentifier}
              autoComplete="username"
              inputMode="numeric"
              autoFocus
            />
            <Field
              id="password"
              label="Password"
              type="password"
              value={password}
              onChange={setPassword}
              autoComplete="current-password"
            />

            {error && (
              <p
                role="alert"
                className="animate-rise rounded-lg bg-danger/12 px-2.5 py-2 text-2xs text-danger"
              >
                {error}
              </p>
            )}

            <Button
              type="submit"
              size="lg"
              disabled={busy || !identifier || !password}
              className="w-full"
            >
              {busy ? <Spinner className="size-4" /> : 'Sign in'}
            </Button>
          </form>

          <p className="mt-5 border-t border-line pt-3 text-center text-2xs leading-relaxed text-fg-subtle">
            Synthetic demo data — fictional company, no real doctors or reps.
            <br />
            Every rep shares a seeded demo password.
          </p>
        </Card>
      </div>
    </div>
  )
}

function Field({
  id,
  label,
  value,
  onChange,
  type = 'text',
  ...rest
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  // Omit the native onChange/value: this component owns them and hands the
  // caller a plain string instead of an event.
} & Omit<InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'value' | 'type' | 'id'>) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-2xs font-medium text-fg-muted">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg outline-none transition-colors focus:border-accent"
        {...rest}
      />
    </div>
  )
}
