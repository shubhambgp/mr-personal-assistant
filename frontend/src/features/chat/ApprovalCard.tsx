// The human in the loop.
//
// Inline in the turn, not a modal. There is no Dialog primitive in this codebase
// and the house pattern for a consequential confirmation is the in-place swap in
// features/conversations/ConversationRow.tsx — so this follows it rather than
// introducing a modal layer for one component.
//
// The layout puts the compliance verdict ABOVE the buttons on purpose. The point
// of the gate is not that a human clicked; it is that a human saw why they were
// being asked.

import { AlertTriangle, Check, Pencil, Send, ShieldCheck, X } from 'lucide-react'
import { useState } from 'react'

import { Badge, Button, Card, Spinner } from '@/components/ui'
import { cx } from '@/lib/cx'
import type { ApprovalCall, ApprovalDecision, ComplianceReview, PendingApproval } from '@/lib/types'

import { KeyValues } from './ToolResult'

/** Fields a rep may change, per tool. Mirrors `approval_editable` in the tool
 *  spec — and the server filters submitted edits against its own copy, so this
 *  is presentation only. A recipient never appears here. */
const LABELS: Record<string, string> = {
  subject: 'Subject',
  body: 'Message',
  title: 'Title',
  notes: 'Notes',
  starts_at: 'Starts',
  duration_minutes: 'Minutes',
}

const MULTILINE = new Set(['body', 'notes'])

/** Read-only argument names, in the rep's vocabulary. CLAUDE.md §1.6: a rep is
 *  not a database administrator, and `thread_id` reads as an internal handle
 *  even though it is Gmail's rather than ours. Unmapped keys are dropped rather
 *  than shown raw — anything worth showing is worth naming. */
const FIXED_LABELS: Record<string, string> = {
  to: 'To',
  thread_id: 'Replying to',
  attendees: 'Invitees',
  notify: 'Send invitations',
  starts_at: 'Starts',
  duration_minutes: 'Length',
}

/** Editable fields are strings or numbers by contract. Anything else would
 *  stringify to "[object Object]" in the input, so it becomes empty instead —
 *  better a blank field the rep can fill than a nonsense one they might send. */
function asText(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

const ACTION_LABEL: Record<string, string> = {
  send_email: 'Send this email?',
  create_event: 'Add this to your calendar?',
}

export function ApprovalCard({
  pending,
  onDecide,
  busy,
}: {
  pending: PendingApproval
  onDecide: (decision: ApprovalDecision) => void
  busy?: boolean
}) {
  const [edits, setEdits] = useState<Record<string, Record<string, string>>>({})
  const [rejecting, setRejecting] = useState(false)

  const review = pending.review
  const blocked = review?.verdict === 'block'

  const decide = (approved: boolean) =>
    onDecide({ interruptId: pending.interruptId, approved, edits: approved ? edits : {} })

  const setField = (callId: string, field: string, value: string) =>
    setEdits((prev) => ({ ...prev, [callId]: { ...prev[callId], [field]: value } }))

  return (
    <Card className="overflow-hidden">
      <header
        className={cx(
          'flex flex-wrap items-center gap-2 border-b border-line px-3 py-2',
          blocked ? 'bg-danger/12' : review?.verdict === 'warn' ? 'bg-warning/12' : 'bg-sunken',
        )}
      >
        <span className="text-label uppercase text-fg-muted">Needs your approval</span>
        {review && <VerdictBadge review={review} />}
      </header>

      <div className="flex flex-col gap-3 p-3">
        {pending.calls.map((call) => (
          <CallBlock
            key={call.id}
            call={call}
            edits={edits[call.id] ?? {}}
            onField={(field, value) => setField(call.id, field, value)}
            disabled={busy}
          />
        ))}

        {review && <Findings review={review} />}

        <div className="flex flex-wrap items-center gap-2">
          {rejecting ? (
            /* The in-place danger swap, as elsewhere in the app. */
            <div className="flex flex-1 items-center gap-1 rounded-lg bg-danger/12 px-2 py-1">
              <span className="min-w-0 flex-1 text-2xs text-danger">Discard this draft?</span>
              <Button variant="danger" size="sm" onClick={() => decide(false)} disabled={busy}>
                <Check className="size-3.5" aria-hidden="true" />
                Discard
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setRejecting(false)}>
                <X className="size-3.5" aria-hidden="true" />
                Keep
              </Button>
            </div>
          ) : (
            <>
              <Button
                variant="primary"
                size="md"
                onClick={() => decide(true)}
                disabled={busy || blocked}
                /* Blocked drafts cannot be sent, and the title says why rather
                   than leaving a dead button. A compliance control the sender
                   can click through is a warning, not a control. */
                title={
                  blocked
                    ? 'This draft breaches an approved rule. Edit the wording, or discard it.'
                    : undefined
                }
              >
                {busy ? <Spinner /> : <Send className="size-3.5" aria-hidden="true" />}
                {blocked ? 'Blocked' : 'Approve and send'}
              </Button>
              <Button variant="ghost" size="md" onClick={() => setRejecting(true)} disabled={busy}>
                Discard
              </Button>
              {Object.keys(edits).length > 0 && (
                <span className="flex items-center gap-1 text-2xs text-fg-subtle">
                  <Pencil className="size-3" aria-hidden="true" />
                  edited
                </span>
              )}
            </>
          )}
        </div>
      </div>
    </Card>
  )
}

function VerdictBadge({ review }: { review: ComplianceReview }) {
  if (review.verdict === 'block') {
    return (
      <Badge tone="danger">
        <AlertTriangle className="size-3" aria-hidden="true" />
        blocked by compliance
      </Badge>
    )
  }
  if (review.verdict === 'warn') {
    return (
      <Badge tone="warning">
        <AlertTriangle className="size-3" aria-hidden="true" />
        check before sending
      </Badge>
    )
  }
  return (
    <Badge tone="success">
      <ShieldCheck className="size-3" aria-hidden="true" />
      compliance checked
    </Badge>
  )
}

function CallBlock({
  call,
  edits,
  onField,
  disabled,
}: {
  call: ApprovalCall
  edits: Record<string, string>
  onField: (field: string, value: string) => void
  disabled?: boolean
}) {
  const editable = new Set(call.editable)
  // Everything the rep may NOT change — shown as a definition list, because a
  // <dl> is the honest way to say "this is not yours to edit". The recipient is
  // deliberately in here: an editable `to` would turn this card into an
  // exfiltration channel.
  const fixed = Object.fromEntries(
    Object.entries(call.args)
      .filter(([key, value]) => !editable.has(key) && value != null && FIXED_LABELS[key])
      .map(([key, value]) => [
        FIXED_LABELS[key] as string,
        key === 'thread_id' ? 'this thread' : value,
      ]),
  )

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="text-xs font-medium text-fg">{ACTION_LABEL[call.name] ?? call.name}</p>

      {Object.keys(fixed).length > 0 && (
        <div className="rounded-lg bg-sunken px-2.5 py-2">
          <KeyValues data={fixed} />
        </div>
      )}

      {call.editable.map((field) => {
        const current = edits[field] ?? asText(call.args[field])
        const label = LABELS[field] ?? field
        return (
          <label key={field} className="flex min-w-0 flex-col gap-1">
            <span className="text-label uppercase text-fg-subtle">{label}</span>
            {MULTILINE.has(field) ? (
              <textarea
                value={current}
                rows={6}
                disabled={disabled}
                onChange={(e) => onField(field, e.target.value)}
                className="scrollbar-thin w-full resize-y rounded-lg border border-line bg-surface px-2 py-1.5 text-xs text-fg outline-none focus:border-accent disabled:opacity-60"
              />
            ) : (
              <input
                value={current}
                disabled={disabled}
                onChange={(e) => onField(field, e.target.value)}
                className="w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-xs text-fg outline-none focus:border-accent disabled:opacity-60"
              />
            )}
          </label>
        )
      })}
    </div>
  )
}

function Findings({ review }: { review: ComplianceReview }) {
  if (!review.findings.length && !review.note && !review.requires_escalation) return null

  return (
    <div className="flex flex-col gap-2">
      {review.requires_escalation === 'pharmacovigilance' && (
        <p className="rounded-lg bg-danger/12 px-2.5 py-2 text-2xs text-danger">
          This thread mentions a possible adverse event. It must go to pharmacovigilance within 24
          hours (SOP-PV-01 §2.1), and the clinical question must not be answered here.
        </p>
      )}
      {review.requires_escalation === 'medical_information' && (
        <p className="rounded-lg bg-warning/12 px-2.5 py-2 text-2xs text-warning">
          This needs a Medical Information request rather than an answer from the field.
        </p>
      )}

      {review.findings.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {review.findings.map((finding, i) => (
            <li key={`${finding.rule}-${i}`} className="rounded-lg bg-overlay/6 px-2.5 py-2">
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <Badge tone={finding.severity === 'block' ? 'danger' : 'warning'}>
                  {finding.rule.replace(/_/g, ' ')}
                </Badge>
                <span className="text-2xs text-fg-subtle">{finding.basis}</span>
              </div>
              {/* The exact span from the draft. A finding the reviewer could not
                  quote is dropped server-side, so this always points somewhere. */}
              <p className="text-2xs text-fg-muted">
                <q className="font-medium text-fg">{finding.quote}</q> — {finding.guidance}
              </p>
            </li>
          ))}
        </ul>
      )}

      {review.note && <p className="text-2xs text-fg-subtle">{review.note}</p>}
    </div>
  )
}
