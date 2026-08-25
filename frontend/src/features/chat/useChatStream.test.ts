// The frontend half of the SSE contract. The backend's evals cannot see this
// layer (they bypass HTTP — ENGINEERING_LOG 16), so these tests are the only
// thing standing between a reducer regression and a silently broken chat.
//
// Every test goes through the REAL patchMessage, so the clone semantics under
// test are the ones production uses — not a reimplementation of them.

import { describe, expect, it } from 'vitest'

import type { ChatMessage, StreamEvent } from '@/lib/types'

import { applyEvent, patchMessage } from './useChatStream'

function message(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'a1',
    role: 'assistant',
    content: '',
    toolCalls: [],
    notices: [],
    streaming: true,
    ...overrides,
  }
}

/** Runs one event through applyEvent exactly the way the hook does. */
function reduce(msg: ChatMessage, event: StreamEvent): ChatMessage {
  let next = msg
  const patch = (fn: (draft: ChatMessage) => void) => {
    next = patchMessage(next, fn)
  }
  applyEvent(event, patch)
  return next
}

describe('applyEvent', () => {
  it('start reports the conversation id and touches nothing', () => {
    const msg = message()
    let reported: string | null = null
    applyEvent(
      { type: 'start', conversation_id: 'c-9' },
      () => {
        throw new Error('start must not patch the message')
      },
      (id) => {
        reported = id
      },
    )
    expect(reported).toBe('c-9')
    expect(msg).toEqual(message())
  })

  it('tool_start appends a running call', () => {
    const next = reduce(message(), {
      type: 'tool_start',
      call_id: 't1',
      name: 'run_sql',
      input: { q: 'x' },
    })
    expect(next.toolCalls).toEqual([
      { callId: 't1', name: 'run_sql', input: { q: 'x' }, status: 'running' },
    ])
  })

  it('tool_end REPLACES the element and never mutates prior state (M-FE3)', () => {
    const started = reduce(message(), {
      type: 'tool_start',
      call_id: 't1',
      name: 'run_sql',
      input: {},
    })
    const runningCall = started.toolCalls[0]

    const done = reduce(started, {
      type: 'tool_end',
      call_id: 't1',
      name: 'run_sql',
      input: {},
      output: '{"rows": []}',
      is_error: false,
      duration_ms: 12,
    })

    // The finished element is a NEW object — memoized rows re-render.
    expect(done.toolCalls[0]).not.toBe(runningCall)
    expect(done.toolCalls[0]?.status).toBe('done')
    expect(done.toolCalls[0]?.output).toBe('{"rows": []}')
    // History was not rewritten: the pre-event state still says running.
    expect(runningCall?.status).toBe('running')
    expect(started.toolCalls[0]?.status).toBe('running')
  })

  it('tool_end for an unseen call id pushes a completed call', () => {
    const next = reduce(message(), {
      type: 'tool_end',
      call_id: 'ghost',
      name: 'search_literature',
      input: {},
      output: 'x',
      is_error: true,
      duration_ms: 5,
    })
    expect(next.toolCalls).toHaveLength(1)
    expect(next.toolCalls[0]?.status).toBe('error')
  })

  it('token appends to content', () => {
    const a = reduce(message({ content: 'Hel' }), { type: 'token', delta: 'lo' })
    expect(a.content).toBe('Hello')
  })

  it('grounding and notice land where the UI reads them', () => {
    let m = reduce(message(), {
      type: 'grounding',
      grounded: false,
      unverified_claims: ['42'],
    })
    m = reduce(m, { type: 'notice', message: 'careful' })
    expect(m.grounding).toEqual({ grounded: false, unverifiedClaims: ['42'] })
    expect(m.notices).toEqual(['careful'])
  })

  it('done stops streaming and stores nothing else', () => {
    const next = reduce(message(), {
      type: 'done',
      conversation_id: 'c',
      response_id: null,
      usage: { input_tokens: 1, output_tokens: 1, cached_tokens: 0 },
      timing: { total_ms: 100, db_ms: 5, db_share_pct: 5 },
    })
    expect(next.streaming).toBe(false)
    // The duration UI was removed on purpose; timing must not creep back in.
    expect(Object.keys(next)).not.toContain('timing')
  })

  it('approval_required stops the stream and carries the card', () => {
    const next = reduce(message(), {
      type: 'approval_required',
      conversation_id: 'c',
      interrupt_id: 'i-1',
      calls: [{ id: 'x', name: 'send_email', args: { to: 'a@b' }, editable: ['body'] }],
      review: null,
    })
    expect(next.streaming).toBe(false)
    expect(next.pendingApproval?.interruptId).toBe('i-1')
    expect(next.pendingApproval?.calls[0]?.name).toBe('send_email')
  })

  it('error surfaces as a notice and ends the stream', () => {
    const next = reduce(message(), { type: 'error', message: 'boom' })
    expect(next.streaming).toBe(false)
    expect(next.notices).toEqual(['boom'])
  })
})

describe('patchMessage', () => {
  it('clones the arrays it documents, and only those', () => {
    const before = message({ notices: ['n'], toolCalls: [] })
    const after = patchMessage(before, (d) => {
      d.notices.push('m')
    })
    expect(after).not.toBe(before)
    expect(after.notices).toEqual(['n', 'm'])
    expect(before.notices).toEqual(['n']) // the original is untouched
  })
})
