// The only place a render-time throw can be caught — which makes this the only
// thing standing between "one component crashed" and "the rep gets a permanent
// white screen with no way back". A class component because React exposes
// getDerivedStateFromError to classes only; nothing else here needs one.
//
// The raw error text is deliberately NOT shown: internal strings never reach
// the rep (the same rule the backend's chat._explain applies to exceptions).
// It still goes to the console for whoever is debugging.

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Replaces the default full-page card — used by App to give the chat area
   *  its own, smaller fallback so one bad turn cannot take down the shell. */
  fallback?: ReactNode
}

interface State {
  failed: boolean
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // No telemetry exists in this app; the console is the honest destination.
    console.error('render error caught by ErrorBoundary', error, info)
  }

  render() {
    if (!this.state.failed) return this.props.children
    if (this.props.fallback) return this.props.fallback
    return <ErrorFallback className="min-h-dvh" />
  }
}

/** Token-styled, self-contained (no ui/ imports): if the crash came from a
 *  shared primitive, importing it here would crash the fallback too. */
export function ErrorFallback({
  className = '',
  title = 'Something went wrong',
  body = 'The app hit an unexpected error. Reloading usually fixes it — your conversations are saved on the server.',
}: {
  className?: string
  title?: string
  body?: string
}) {
  return (
    <div className={`flex items-center justify-center bg-page p-6 ${className}`}>
      <div className="w-full max-w-sm rounded-card border border-line bg-surface p-6 text-center shadow-lift">
        <h1 className="font-serif text-xl text-fg">{title}</h1>
        <p className="mt-2 text-xs text-fg-muted">{body}</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-5 inline-flex h-10 items-center justify-center rounded-xl bg-accent px-4 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-ink"
        >
          Reload
        </button>
      </div>
    </div>
  )
}
