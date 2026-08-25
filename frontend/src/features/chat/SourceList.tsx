// Where the answer came from.
//
// Derived entirely from the turn's `search_literature` tool calls, which are
// already streamed, already persisted in `messages.tool_calls`, and already
// replayed when a conversation is reopened. So this needs no new SSE event, no
// new API field and no backend change — the provenance was always in the
// payload, it just was not on screen.
//
// It matters more here than in a general chatbot: a rep repeating a dosing or
// interaction statement to a doctor needs to be able to say where it came from,
// and "Cardevia SmPC §4.5, p1" is checkable in a way that a confident sentence
// is not.

import { useMemo, useState } from 'react'
import { BookOpen, ChevronDown } from 'lucide-react'

import { cx } from '@/lib/cx'
import type { ToolCall } from '@/lib/types'

import { extractSources } from './sources'

const DOC_TYPE_LABEL: Record<string, string> = {
  monograph: 'SmPC',
  detailing_aid: 'Detailing aid',
  sop: 'SOP',
  brief: 'Brief',
}

export function SourceList({ toolCalls }: { toolCalls: ToolCall[] }) {
  const sources = useMemo(() => extractSources(toolCalls), [toolCalls])
  // Collapsed by default: the answer is the point, and provenance should be one
  // glance away rather than competing with it.
  const [open, setOpen] = useState(false)

  if (!sources.length) return null

  return (
    <div className="rounded-card border border-line bg-surface/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cx(
          'flex w-full items-center gap-2 rounded-card px-3 py-2 text-left transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent',
          'hover:bg-overlay/4',
        )}
      >
        <BookOpen className="size-3.5 shrink-0 text-accent-ink" aria-hidden="true" />
        <span className="flex-1 text-2xs font-medium text-fg-muted">
          {sources.length} source{sources.length === 1 ? '' : 's'} from your product literature
        </span>
        <ChevronDown
          aria-hidden="true"
          className={cx(
            'size-3.5 shrink-0 text-fg-subtle transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>

      {open && (
        <ol className="animate-rise space-y-1.5 border-t border-line px-3 py-2.5">
          {sources?.map((source, i) => (
            <li key={`${source.document}-${source.section}-${i}`} className="flex gap-2 text-2xs">
              <span className="shrink-0 tabular-nums text-fg-subtle">[{i + 1}]</span>
              <span className="min-w-0">
                <span className="font-medium text-fg">{source.document}</span>
                {source.docType && DOC_TYPE_LABEL[source.docType] && (
                  <span className="ml-1.5 rounded-sm bg-overlay/8 px-1 py-0.5 text-fg-subtle">
                    {DOC_TYPE_LABEL[source.docType]}
                  </span>
                )}
                <span className="block text-fg-subtle">
                  {source.section || 'front matter'}
                  {source.page !== null && ` · page ${source.page}`}
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
