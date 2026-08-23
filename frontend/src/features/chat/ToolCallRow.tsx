// One step in the agent's work: a status line, and deliberately nothing more.
//
// This row used to expand into the raw internals — the SQL, the arguments, the
// full result table. That data is still recorded server-side (the audit trail
// and the source citations need it), but it is NEVER rendered here: the rep
// asked a question in their own language and the internals answer a different
// audience. What a step shows is what it DID (a friendly label), whether it
// worked, and how long it took. The one exception is an error message, because
// an error the rep cannot read is a failure they cannot explain.

import { useMemo } from 'react'
import { AlertCircle, Check } from 'lucide-react'

import { Spinner } from '@/components/ui'
import { cx } from '@/lib/cx'
import type { ToolCall, ToolPayload } from '@/lib/types'

import { toolMeta } from './toolMeta'

export function ToolCallRow({ call, index }: { call: ToolCall; index: number }) {
  const { label, Icon } = toolMeta(call.name)

  // Parsed ONLY to detect a soft error — `{"error": …}` returned rather than
  // raised. The payload's data never reaches the screen.
  const softError = useMemo<string | null>(() => {
    if (!call.output) return null
    try {
      const payload = JSON.parse(call.output) as ToolPayload
      return typeof payload.error === 'string' ? payload.error : null
    } catch {
      return null
    }
  }, [call.output])

  const failed = call.status === 'error' || Boolean(softError)
  const running = call.status === 'running'
  const status = running ? 'running' : failed ? 'failed' : 'done'

  return (
    <li
      /* Slides in from the rail, staggered by position, so the timeline reads
         as steps taken in order rather than a batch dumped at once. */
      className="animate-slide-in"
      style={{ animationDelay: `${Math.min(index * 40, 320)}ms` }}
    >
      <div
        className={cx(
          'flex w-full items-center gap-2 rounded-lg py-1.5 pl-2 pr-1.5',
          // Only the error state gets a wash; a successful call is chrome.
          failed && 'bg-danger/8',
        )}
      >
        <StatusIcon running={running} failed={failed} />

        <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs font-medium text-fg-muted">
          {/* Identity, not internals: which kind of work happened. */}
          <Icon className="size-3.5 shrink-0 text-fg-subtle" aria-hidden="true" />
          <span className="truncate">{label}</span>
          {/* lucide sets aria-hidden on its own svg, so colour and glyph carry
              no meaning for a screen reader without this. */}
          <span className="sr-only">— {status}</span>
        </span>
        {/* No duration badge, deliberately: how long a query took is plumbing,
            not something a rep asked. The spinner already says "in progress". */}
      </div>

      {/* The exception to "no internals": an error is a message TO the rep. */}
      {softError && (
        <p className="py-1 pl-8 pr-1.5 text-2xs text-danger">{softError}</p>
      )}
    </li>
  )
}

// useLiveElapsed was removed with the duration badge — nothing here ticks
// any more, so a settled row never re-renders at all.

function StatusIcon({ running, failed }: { running: boolean; failed: boolean }) {
  return (
    <span className="flex size-4 shrink-0 items-center justify-center">
      {running ? (
        <Spinner className="size-3.5 text-accent-ink" />
      ) : failed ? (
        <AlertCircle className="size-3.5 text-danger" aria-hidden="true" />
      ) : (
        /* Spring-eased pop on the check: the moment a step completes should be
           unmissable, and it is the one place a bouncy easing is honest. */
        <span className="animate-pop relative flex size-4 items-center justify-center">
          <Check className="size-3.5 text-success" aria-hidden="true" />
        </span>
      )}
    </span>
  )
}

