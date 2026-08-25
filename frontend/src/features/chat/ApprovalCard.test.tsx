// @vitest-environment jsdom
//
// The most consequential UI in the app: what a rep approves is what gets sent
// to a prescriber. Three properties are worth a DOM test — a blocked draft
// cannot be approved, edits travel with the approval, and a discard sends no
// edits at all.

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PendingApproval } from '@/lib/types'

import { ApprovalCard } from './ApprovalCard'

afterEach(cleanup)

function pending(overrides: Partial<PendingApproval> = {}): PendingApproval {
  return {
    interruptId: 'i-1',
    calls: [
      {
        id: 'call-1',
        name: 'send_email',
        args: { to: 'dr@clinic.example', subject: 'Dosing card', body: 'Hello doctor' },
        editable: ['subject', 'body'],
      },
    ],
    review: null,
    ...overrides,
  }
}

describe('ApprovalCard', () => {
  it('a blocked verdict disables Approve — a control you can click through is a warning', () => {
    render(
      <ApprovalCard
        pending={pending({
          review: {
            verdict: 'block',
            findings: [],
            requires_escalation: null,
            reviewed_by: 'reviewer',
          },
        })}
        onDecide={() => undefined}
      />,
    )
    const approve = screen.getByRole('button', { name: /blocked/i })
    expect((approve as HTMLButtonElement).disabled).toBe(true)
  })

  it('editing a field sends the edit keyed by call id', () => {
    const onDecide = vi.fn()
    render(<ApprovalCard pending={pending()} onDecide={onDecide} />)

    fireEvent.change(screen.getByLabelText('Subject'), {
      target: { value: 'Dosing card — elderly wording' },
    })
    fireEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    expect(onDecide).toHaveBeenCalledWith({
      interruptId: 'i-1',
      approved: true,
      edits: { 'call-1': { subject: 'Dosing card — elderly wording' } },
    })
  })

  it('discard confirms in place, then decides false with NO edits', () => {
    const onDecide = vi.fn()
    render(<ApprovalCard pending={pending()} onDecide={onDecide} />)

    // Even an edited draft sends no edits on discard — nothing is applied.
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'changed' } })
    fireEvent.click(screen.getByRole('button', { name: /^discard$/i }))
    // First click swaps to the in-place confirmation; nothing decided yet.
    expect(onDecide).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /discard/i }))
    expect(onDecide).toHaveBeenCalledWith({ interruptId: 'i-1', approved: false, edits: {} })
  })

  it('the recipient is rendered read-only, never as an input', () => {
    render(<ApprovalCard pending={pending()} onDecide={() => undefined} />)
    // `to` appears in the fixed <dl>, and no form control carries its value.
    expect(screen.getByText('dr@clinic.example')).toBeTruthy()
    const inputs = [
      ...document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea'),
    ]
    expect(inputs.some((el) => el.value.includes('dr@clinic.example'))).toBe(false)
  })
})
