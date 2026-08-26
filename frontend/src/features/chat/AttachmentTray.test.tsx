// @vitest-environment jsdom
//
// The thumbnail must survive StrictMode. This is a component test rather than a
// pure-function one because the bug lived entirely in the lifecycle: the Thumb
// created its own object URL and revoked it in an effect cleanup, and
// StrictMode's mount → cleanup → mount revoked it and never recreated it. Every
// preview in development was a dead blob, and the tray showed a broken image for
// a perfectly good photo.

import { StrictMode } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AttachmentTray } from './AttachmentTray'
import { releasePreviews, toPickedImages } from './attachments'

/** A stand-in for the browser's blob registry, so a revoked URL is observable. */
const live = new Set<string>()
let seq = 0

beforeEach(() => {
  live.clear()
  seq = 0
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: () => {
      const url = `blob:test/${++seq}`
      live.add(url)
      return url
    },
    revokeObjectURL: (url: string) => live.delete(url),
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const png = (name = 'rcpa.png') => new File(['x'], name, { type: 'image/png' })

describe('AttachmentTray', () => {
  it('renders a thumbnail whose URL is still live under StrictMode', () => {
    const images = toPickedImages([png()])

    render(
      <StrictMode>
        <AttachmentTray files={images} onRemove={() => {}} />
      </StrictMode>,
    )

    const img = screen.getByAltText('rcpa.png')
    expect(img.getAttribute('src')).toBe(images[0]?.url)
    expect(live.has(img.getAttribute('src') ?? '')).toBe(true)
  })

  it('does not revoke the URL when the tray unmounts', () => {
    // The URL belongs to the file, which the composer owns. A tray that revoked
    // on unmount is exactly what StrictMode broke.
    const images = toPickedImages([png()])
    const { unmount } = render(<AttachmentTray files={images} onRemove={() => {}} />)
    unmount()
    expect(live.has(images[0]?.url ?? '')).toBe(true)
  })

  it('releases a preview only when the owner says so', () => {
    const images = toPickedImages([png()])
    expect(live.size).toBe(1)
    releasePreviews(images)
    expect(live.size).toBe(0)
  })

  it('falls back to a labelled tile when the image cannot decode', () => {
    const images = toPickedImages([png('truncated.jpg')])
    render(<AttachmentTray files={images} onRemove={() => {}} />)

    // fireEvent, not dispatchEvent: the handler sets state, and that has to be
    // flushed inside act() before the fallback is in the tree.
    fireEvent.error(screen.getByAltText('truncated.jpg'))

    // The filename survives; the broken-image glyph does not appear.
    expect(screen.queryByAltText('truncated.jpg')).toBeNull()
    expect(screen.getByText('truncated.jpg')).toBeTruthy()
  })

  it('renders documents as Library chips, not as images', () => {
    render(
      <AttachmentTray
        files={[]}
        onRemove={() => {}}
        documents={[new File(['x'], 'sop.pdf', { type: 'application/pdf' })]}
      />,
    )
    expect(screen.getByText('sop.pdf')).toBeTruthy()
    expect(screen.getByText('adds to your Library')).toBeTruthy()
    expect(screen.queryByAltText('sop.pdf')).toBeNull()
  })
})
