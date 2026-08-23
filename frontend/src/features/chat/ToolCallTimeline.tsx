// The agent's work, presented the way Claude/ChatGPT present it: while the
// turn is LIVE the reader sees one friendly activity line ("Searching product
// literature…") with a live timer — not a growing pile of query internals.
// Once the turn settles, the whole process collapses into a single quiet
// summary row ("Worked for 4.2s · 3 steps") that expands on click into the
// step-by-step timeline, where each step can be opened further for the SQL,
// arguments and rows. Transparency on demand; the ANSWER is the page.

import { useState } from 'react'
import { AlertCircle, Check, ChevronRight, Sparkles } from 'lucide-react'

import { Badge, Spinner } from '@/components/ui'
import { cx } from '@/lib/cx'
import type { ToolCall } from '@/lib/types'

import { ToolCallRow } from './ToolCallRow'
import { toolMeta } from './toolMeta'

export function ToolCallTimeline({
  calls,
  streaming = false,
}: {
  calls: ToolCall[]
  /** The turn is still producing output. Live view shows the current activity;
   *  the settled view is the collapsed summary. */
  streaming?: boolean
}) {
  // null = the reader has not decided; a failed step decides for them, because
  // an error you have to click to discover is an error nobody reads.
  const [toggled, setToggled] = useState<boolean | null>(null)

  if (!calls.length) return null

  const failed = calls.some(
    (c) => c.status === 'error' || (c.output ?? '').trimStart().startsWith('{"error"'),
  )
  const active = calls.find((c) => c.status === 'running')
  const live = streaming || active !== undefined
  const open = toggled ?? failed

  if (live && !open) {
    const doneCount = calls.filter((c) => c.status !== 'running').length
    return (
      <div className="flex flex-col gap-1">
        {doneCount > 0 && (
          <button
            type="button"
            onClick={() => setToggled(true)}
            aria-expanded={false}
            className="flex w-fit items-center gap-1 rounded-lg px-2 py-1 text-2xs text-fg-subtle transition-colors hover:bg-overlay/6 hover:text-fg-muted"
          >
            <Check className="size-3 text-success" aria-hidden="true" />
            {doneCount} step{doneCount === 1 ? '' : 's'} done
            <ChevronRight className="size-3" aria-hidden="true" />
          </button>
        )}
        <ActivityLine call={active ?? calls[calls.length - 1]} />
      </div>
    )
  }

  return (
    <div className="flex flex-col">
      <button
        type="button"
        onClick={() => setToggled(!open)}
        aria-expanded={open}
        className={cx(
          'flex w-fit max-w-full items-center gap-1.5 rounded-lg px-2 py-1 text-left transition-colors',
          'text-2xs text-fg-subtle hover:bg-overlay/6 hover:text-fg-muted',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent',
        )}
      >
        {failed ? (
          <AlertCircle className="size-3.5 shrink-0 text-danger" aria-hidden="true" />
        ) : (
          <Sparkles className="size-3.5 shrink-0 text-fg-subtle" aria-hidden="true" />
        )}
        {/* No duration, deliberately: "how long" is plumbing. What matters is
            that work happened and can be inspected. */}
        <span className="truncate">
          Worked through {calls.length} step{calls.length === 1 ? '' : 's'}
        </span>
        {failed && <Badge tone="danger">issue</Badge>}
        <ChevronRight
          aria-hidden="true"
          className={cx('size-3 shrink-0 transition-transform', open && 'rotate-90')}
        />
      </button>

      {open && (
        <div className="animate-rise relative mt-1 pl-3">
          <span
            aria-hidden="true"
            className="animate-rail absolute inset-y-1 left-0 w-px origin-top bg-line-strong"
          />
          <ul className="flex flex-col gap-0.5" aria-label="Steps taken">
            {calls.map((call, i) => (
              <ToolCallRow key={call.callId} call={call} index={i} />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** What the agent is doing right now, in the rep's language. No timer — the
 *  spinner already says "in progress", and elapsed milliseconds are plumbing. */
function ActivityLine({ call }: { call: ToolCall | undefined }) {
  if (!call) return null
  const { label, Icon } = toolMeta(call.name)
  const running = call.status === 'running'

  return (
    <p
      role="status"
      className="flex items-center gap-2 rounded-lg bg-accent-soft/60 px-2.5 py-1.5 text-xs text-fg-muted"
    >
      {running ? (
        <Spinner className="size-3.5 shrink-0 text-accent-ink" />
      ) : (
        <Check className="size-3.5 shrink-0 text-success" aria-hidden="true" />
      )}
      <Icon className="size-3.5 shrink-0 text-accent-ink" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate">
        {label}
        {running && '…'}
      </span>
    </p>
  )
}
