import { memo } from 'react'
import { AlertTriangle, Paperclip } from 'lucide-react'

import { Badge } from '@/components/ui'
import type { ApprovalDecision, ChatMessage } from '@/lib/types'

import { ApprovalCard } from './ApprovalCard'
import { SourceList } from './SourceList'
import { StreamingText } from './StreamingText'
import { ToolCallTimeline } from './ToolCallTimeline'

/** memo() is load-bearing during streaming, not a micro-optimisation. Each token
 *  publishes a NEW messages array, so MessageList re-renders every turn; without
 *  this, every completed turn (its tool timeline, rows, sources, approval card)
 *  re-renders on every token of the answer still streaming. Untouched messages
 *  keep referential identity (the reducer's map returns them unchanged) and
 *  onDecide is useCallback-stable, so this confines per-token work to the one
 *  streaming turn. See audit finding H8. */
export const MessageTurn = memo(function MessageTurn({
  message,
  onDecide,
}: {
  message: ChatMessage
  /** Threaded as a prop rather than pulled from a context: one consumer, one
   *  linear path, and a required prop is the loudest possible way to express the
   *  dependency. A context earns its keep when a second chat-level action
   *  arrives. */
  onDecide?: (messageId: string, decision: ApprovalDecision) => void
}) {
  return message.role === 'user' ? (
    <UserTurn message={message} />
  ) : (
    <AssistantTurn message={message} onDecide={onDecide} />
  )
})

/** A warm neutral pill, not a saturated accent bubble: Claude does not colour
 *  the user's own words, and a filled accent block beside serif prose pulls
 *  the eye to the question instead of the answer. */
function UserTurn({ message }: { message: ChatMessage }) {
  return (
    <div className="animate-rise flex justify-end">
      <div className="max-w-[88%] rounded-card rounded-br-md bg-sunken px-3.5 py-2 lg:max-w-[75%]">
        {!!message.attachments?.length && (
          <ul className="mb-2 flex flex-wrap gap-1.5">
            {message.attachments.map((file, i) => (
              <li key={`${file.name}-${i}`}>
                {file.previewUrl ? (
                  <img
                    src={file.previewUrl}
                    alt={file.name}
                    className="size-14 rounded-lg border border-line object-cover"
                  />
                ) : (
                  /* A resumed conversation has the filename and nothing to
                     render: images are never persisted server-side. This chip
                     is the honest fallback, not a broken <img>. */
                  <span className="flex items-center gap-1 rounded-lg bg-overlay/8 px-2 py-1 text-2xs text-fg-subtle">
                    <Paperclip className="size-3" aria-hidden="true" />
                    <span className="max-w-40 truncate">{file.name}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="whitespace-pre-wrap break-words text-sm text-fg">{message.content}</p>
      </div>
    </div>
  )
}

function AssistantTurn({
  message,
  onDecide,
}: {
  message: ChatMessage
  onDecide?: (messageId: string, decision: ApprovalDecision) => void
}) {
  const thinking = message.streaming && !message.content && !message.toolCalls.length

  return (
    <div className="animate-rise flex flex-col gap-2">
      <ToolCallTimeline calls={message.toolCalls} streaming={message.streaming} />

      {thinking && <Thinking />}

      {(message.content || message.streaming) && (
        /* A screen reader got nothing at all as tokens arrived. `aria-atomic`
           false so it announces the additions, not the whole answer again on
           every token. */
        <div aria-live="polite" aria-atomic="false">
          <StreamingText content={message.content} streaming={message.streaming} />
        </div>
      )}

      {/* Provenance, after the answer: the answer is what the rep came for. */}
      {!message.streaming && <SourceList toolCalls={message.toolCalls} />}

      {message.grounding && !message.grounding.grounded && (
        <GroundingWarning claims={message.grounding.unverifiedClaims} />
      )}

      {/* After the warnings, so every caveat is above the decision. */}
      {message.pendingApproval && onDecide && (
        <>
          {/* The most consequential UI in the app used to arrive in silence for
              a screen-reader user — the stream just stopped. The card itself is
              not a live region (re-announcing every field on each render would
              be noise); this one-line status is. */}
          <p role="status" className="sr-only">
            Approval needed — review the action below and approve or decline.
          </p>
          <ApprovalCard
            pending={message.pendingApproval}
            busy={message.streaming}
            onDecide={(decision) => onDecide(message.id, decision)}
          />
        </>
      )}

      {message.notices.map((notice, i) => (
        <p
          key={i}
          role="status"
          className="rounded-lg bg-overlay/6 px-2.5 py-1.5 text-2xs text-fg-muted"
        >
          {notice}
        </p>
      ))}

      {/* The per-turn timing line ("12.3s total · database 2%") was removed on
          purpose: durations are plumbing, and /api/metrics still records them
          for whoever actually needs the number. */}
    </div>
  )
}

/** A calm wave rather than `animate-bounce`, which reads as a toy. */
function Thinking() {
  return (
    <p className="flex items-center gap-2 text-2xs text-fg-subtle">
      <span className="flex gap-1" aria-hidden="true">
        {[0, 140, 280].map((delay) => (
          <span
            key={delay}
            className="animate-wave size-1.5 rounded-full bg-fg-subtle"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </span>
      thinking…
    </p>
  )
}

function GroundingWarning({ claims }: { claims: string[] }) {
  return (
    <div role="status" className="rounded-card bg-warning/12 px-3 py-2">
      <div className="mb-1 flex items-center gap-1.5">
        <Badge tone="warning">
          <AlertTriangle className="size-3" aria-hidden="true" />
          unverified
        </Badge>
        <span className="text-2xs font-medium text-warning">
          Some numbers were not confirmed by a tool result
        </span>
      </div>
      <p className="text-2xs text-fg-muted">
        {claims.join(', ')} — treat these as unverified and check before quoting them.
      </p>
    </div>
  )
}
