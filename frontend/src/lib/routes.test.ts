import { describe, expect, it } from 'vitest'

import { pathForView, ROUTES, viewForPath } from './routes'
import type { View } from './routes'

describe('routes', () => {
  const VIEWS: View[] = ['chat', 'agenda', 'settings', 'library']

  it('every view round-trips through its own path', () => {
    for (const view of VIEWS) {
      expect(viewForPath(pathForView(view))).toBe(view)
    }
  })

  it('a conversation URL is the chat view', () => {
    expect(viewForPath(ROUTES.conversation('abc-123'))).toBe('chat')
  })

  it('unknown paths fall back to chat — App renders the 404 for those', () => {
    expect(viewForPath('/definitely-not-a-page')).toBe('chat')
  })
})
