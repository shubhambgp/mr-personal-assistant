// Owns the SSE read loop and the reducer over the backend's event union.
//
// Why fetch() and not EventSource: EventSource is GET-only and cannot send
// credentials selectively or a multipart body (image upload). So we POST and
// read response.body as a stream ourselves. That also gives us AbortController,
// which is what the Stop button needs.

import { useCallback, useEffect, useRef, useState } from 'react'
import { AUTH_EXPIRED_EVENT } from '@/lib/api'
import type {
  ApprovalDecision,
  ChatMessage,
  IngestedDocument,
  StreamEvent,
  ToolCall,
} from '@/lib/types'

let seq = 0
const nextId = () => `m${Date.now().toString(36)}-${(seq += 1)}`

/** Clone-then-mutate for one message: cheaper than a deep clone of the whole
 *  list on every single token.
 *
 *  NOTE the clone list: only `toolCalls` and `notices` are copied, so any NEW
 *  array field on ChatMessage must be added here or React will not see a push.
 *  `pendingApproval` is deliberately an object replaced wholesale, which is why
 *  it needs no entry. Exported (with applyEvent) so the unit tests exercise the
 *  REAL clone semantics rather than a reimplementation of them. */
export function patchMessage(message: ChatMessage, fn: (draft: ChatMessage) => void): ChatMessage {
  const draft: ChatMessage = {
    ...message,
    toolCalls: [...message.toolCalls],
    notices: [...message.notices],
  }
  fn(draft)
  return draft
}

interface SendOptions {
  message: string
  images?: File[]
  conversationId?: string | null
  /** Documents just ingested into the Library. They serve two purposes and both
   *  matter: their names tell the turn a file arrived (it is never pasted into
   *  the message, so the model would otherwise say "I don't see a PDF"), and
   *  they become attachment chips so the rep can see WHICH file the question was
   *  asked about. Sending the names without rendering them left the transcript
   *  looking like the file had never been attached. */
  documents?: IngestedDocument[]
}

export function useChatStream(onConversationId?: (id: string) => void) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // Abort the in-flight fetch on unmount (e.g. sign-out): without this the
  // stream kept running and setMessages fired on an unmounted hook.
  useEffect(() => () => abortRef.current?.abort(), [])

  const reset = useCallback((seed: ChatMessage[] = []) => {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
    setMessages(seed)
  }, [])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  // Publish one new message object per event via patchMessage — see its note
  // on the clone list.
  const makePatch = useCallback(
    (assistantId: string) => (fn: (draft: ChatMessage) => void) =>
      setMessages((prev) => prev?.map((m) => (m.id === assistantId ? patchMessage(m, fn) : m))),
    [],
  )

  const send = useCallback(
    async ({ message, images, conversationId, documents }: SendOptions) => {
      const userId = nextId()
      const assistantId = nextId()

      setMessages((prev) => [
        ...prev,
        {
          id: userId,
          role: 'user',
          content: message,
          toolCalls: [],
          notices: [],
          // previewUrl is created here and lives for the session. It is not
          // revoked on unmount: the thumbnail must survive as long as the
          // message is on screen, and the list only shrinks on sign-out or
          // conversation switch, both of which drop the page's whole heap.
          attachments: [
            ...(images?.map((f) => ({
              name: f.name,
              size: f.size,
              previewUrl: URL.createObjectURL(f),
              kind: 'image' as const,
            })) ?? []),
            // No previewUrl on purpose: the original bytes are not kept once a
            // document is ingested, so there is nothing to render a thumbnail
            // from and MessageTurn's chip is the honest representation.
            ...(documents?.map((d) => ({
              name: d.name,
              size: d.size,
              kind: 'document' as const,
            })) ?? []),
          ],
        },
        {
          id: assistantId,
          role: 'assistant',
          content: '',
          toolCalls: [],
          notices: [],
          streaming: true,
        },
      ])
      setStreaming(true)

      const patch = makePatch(assistantId)

      const controller = new AbortController()
      abortRef.current = controller

      const form = new FormData()
      form.set('message', message)
      if (conversationId) form.set('conversation_id', conversationId)
      for (const file of images ?? []) form.append('images', file)
      for (const doc of documents ?? []) form.append('document_names', doc.name)

      try {
        const response = await fetch('/api/chat/stream', {
          method: 'POST',
          body: form,
          credentials: 'include',
          signal: controller.signal,
        })

        if (!response.ok || !response.body) {
          patch((d) => {
            d.streaming = false
            d.notices.push(
              response.status === 401
                ? 'Your session expired. Please sign in again.'
                : `Request failed (${response.status}).`,
            )
          })
          // These raw fetches bypass lib/api's request(), so fire the same
          // session-expiry signal ourselves. AuthProvider returns to login.
          if (response.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
          return
        }

        await consume(response.body, patch, onConversationId)
        patch((d) => {
          d.streaming = false
        })
      } catch (error) {
        const aborted = error instanceof DOMException && error.name === 'AbortError'
        patch((d) => {
          d.streaming = false
          d.notices.push(aborted ? 'Stopped.' : 'Connection lost while streaming.')
          for (const call of d.toolCalls) {
            if (call.status === 'running') call.status = 'error'
          }
        })
      } finally {
        setStreaming(false)
        abortRef.current = null
      }
    },
    [makePatch, onConversationId],
  )

  /** Answer a paused turn. Streams the continuation into the SAME message.
   *
   *  Like `send`, this bypasses lib/api.ts: the response is SSE, not JSON, and
   *  api.ts's `request` parses a body. */
  const resume = useCallback(
    async (assistantId: string, decision: ApprovalDecision, conversationId: string) => {
      const patch = makePatch(assistantId)
      // Snapshot before clearing: a transient network failure used to discard
      // the card permanently, wedging the thread until a full reload rebuilt it
      // from the server (audit finding M-FE6). On any failure OTHER than a 409 —
      // where the card genuinely is stale — it is restored so the rep can
      // decide again.
      let snapshot: ChatMessage['pendingApproval']
      patch((d) => {
        snapshot = d.pendingApproval
        // Clear the card immediately. Leaving it up while the continuation
        // streams invites a second click on a decision already taken.
        d.pendingApproval = undefined
        d.streaming = true
      })
      setStreaming(true)

      const controller = new AbortController()
      abortRef.current = controller

      try {
        const response = await fetch('/api/chat/resume', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          signal: controller.signal,
          body: JSON.stringify({
            conversation_id: conversationId,
            interrupt_id: decision.interruptId,
            approved: decision.approved,
            edits: decision.edits,
          }),
        })

        if (!response.ok || !response.body) {
          patch((d) => {
            d.streaming = false
            if (response.status !== 409) d.pendingApproval = snapshot
            d.notices.push(
              response.status === 409
                ? 'That approval is no longer current. Reload this conversation.'
                : response.status === 401
                  ? 'Your session expired. Please sign in again.'
                  : `Could not send your decision (${response.status}). Decide again to retry.`,
            )
          })
          if (response.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
          return
        }

        await consume(response.body, patch, onConversationId)
        patch((d) => {
          d.streaming = false
        })
      } catch (error) {
        const aborted = error instanceof DOMException && error.name === 'AbortError'
        patch((d) => {
          d.streaming = false
          // The decision never reached the server (or the stream died before it
          // settled) — put the card back so the thread is not stuck.
          d.pendingApproval = snapshot
          d.notices.push(
            aborted ? 'Stopped — decide again when ready.' : 'Connection lost while streaming.',
          )
        })
      } finally {
        setStreaming(false)
        abortRef.current = null
      }
    },
    [makePatch, onConversationId],
  )

  return { messages, streaming, send, resume, stop, reset, setMessages }
}

/** Reads one SSE body to completion, applying every frame.
 *
 *  Shared by `send` and `resume` on purpose: the frame splitting is the fiddly
 *  part (a network chunk can cut a frame in half), and two copies would be two
 *  things to keep in step. */
async function consume(
  // NonNullable<Response['body']> rather than ReadableStream<Uint8Array>: the two
  // differ in the buffer type parameter, and TextDecoderStream only accepts the
  // exact one fetch produces.
  body: NonNullable<Response['body']>,
  patch: (fn: (draft: ChatMessage) => void) => void,
  onConversationId?: (id: string) => void,
) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader()
  // SSE frames are separated by a blank line, and a network chunk can split one
  // frame in half — so buffer until we see the terminator.
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += value

    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)

      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (!line) continue

      let event: StreamEvent
      try {
        event = JSON.parse(line.slice(5).trim()) as StreamEvent
      } catch {
        continue // a frame we cannot parse is skipped, not fatal
      }
      applyEvent(event, patch, onConversationId)
    }
  }
}

export function applyEvent(
  event: StreamEvent,
  patch: (fn: (draft: ChatMessage) => void) => void,
  onConversationId?: (id: string) => void,
) {
  switch (event.type) {
    case 'start':
      onConversationId?.(event.conversation_id)
      break

    case 'tool_start':
      patch((d) => {
        const call: ToolCall = {
          callId: event.call_id,
          name: event.name,
          input: event.input,
          status: 'running',
        }
        d.toolCalls.push(call)
      })
      break

    case 'tool_end':
      patch((d) => {
        const finished = {
          output: event.output,
          isError: event.is_error,
          status: event.is_error ? ('error' as const) : ('done' as const),
        }
        const idx = d.toolCalls.findIndex((c) => c.callId === event.call_id)
        const existing = idx >= 0 ? d.toolCalls[idx] : undefined
        // Replace the element, never Object.assign it in place. The draft's
        // toolCalls array is a shallow copy, so its ELEMENTS are still the same
        // objects held by previous React state — mutating one rewrites history
        // and, with memo() on the rows/turn, can leave a row frozen in
        // 'running'. See audit finding M-FE3.
        if (existing) {
          d.toolCalls[idx] = { ...existing, ...finished }
        } else {
          d.toolCalls.push({
            callId: event.call_id,
            name: event.name,
            input: event.input,
            ...finished,
          })
        }
      })
      break

    case 'token':
      patch((d) => {
        d.content += event.delta
      })
      break

    case 'grounding':
      patch((d) => {
        d.grounding = { grounded: event.grounded, unverifiedClaims: event.unverified_claims }
      })
      break

    case 'notice':
      patch((d) => {
        d.notices.push(event.message)
      })
      break

    case 'done':
      // The event also carries timing; it is deliberately not stored — the
      // per-turn duration line was removed from the UI, and /api/metrics is
      // where those numbers live for whoever needs them.
      patch((d) => {
        d.streaming = false
      })
      break

    case 'approval_required':
      patch((d) => {
        // Terminal for this leg: the backend sends no `done`, because the turn
        // has not produced an answer yet.
        d.streaming = false
        d.pendingApproval = {
          interruptId: event.interrupt_id,
          calls: event.calls,
          review: event.review,
        }
      })
      break

    case 'error':
      patch((d) => {
        d.streaming = false
        d.notices.push(event.message)
      })
      break

    default: {
      // A new backend event must be a COMPILE ERROR here, not a silent no-op.
      // The switch had no default, so adding a variant to StreamEvent used to
      // typecheck cleanly and then do nothing at runtime — which is the worst
      // possible failure for a contract whose whole comment says "keep the two
      // in step".
      const unhandled: never = event
      void unhandled
      break
    }
  }
}
