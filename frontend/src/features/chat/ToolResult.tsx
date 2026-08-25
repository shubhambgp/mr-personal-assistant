// Key/value rendering for the approval card's fixed fields.
//
// This module used to also render full tool results — a table over `{rows}`,
// raw JSON with a copy button. Those went with the decision that a tool's
// internals (the query, the returned data) are never shown in the chat: the
// data still reaches the server-side record, but the UI shows only what a step
// DID. KeyValues survives because the approval card must show the rep exactly
// what they are approving.

import { fmtCell } from '@/lib/format'

export function KeyValues({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data)?.filter(([, v]) => v !== null && v !== undefined)
  if (!entries.length) return <span className="text-2xs text-fg-subtle">none</span>
  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-1 text-2xs">
      {entries?.map(([key, value]) => (
        <div key={key} className="flex min-w-0 gap-1">
          <dt className="font-mono text-fg-subtle">{key}</dt>
          <dd className="truncate font-medium text-fg-muted">{fmtCell(value)}</dd>
        </div>
      ))}
    </dl>
  )
}
