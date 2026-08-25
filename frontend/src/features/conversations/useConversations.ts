// Owns the conversation list. Extracted from App so the refresh-after-a-turn
// is an explicit call at the point the turn ends, rather than an effect
// watching a `streaming` flag — the effect version called setState from inside
// an effect body, which react-hooks now (correctly) flags.

import { useCallback, useEffect, useState } from 'react'

import { api } from '@/lib/api'
import type { ConversationSummary } from '@/lib/types'

export function useConversations() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(true)
  // Tracked, not swallowed: an offline rep used to see "No conversations yet.
  // Ask something to start one." — an assertion the app could not back.
  const [error, setError] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setConversations(await api.conversations())
      setError(false)
    } catch {
      /* mid-session refresh failure: keep the list we have, no banner */
    } finally {
      setLoading(false)
    }
  }, [])

  // The mount fetch is written out rather than calling refresh(): state is set
  // in the promise callback, and the `alive` guard stops a late response from
  // setting state after sign-out has unmounted the hook.
  useEffect(() => {
    let alive = true
    api
      .conversations()
      .then((list) => {
        if (alive) {
          setConversations(list)
          setError(false)
        }
      })
      .catch(() => {
        if (alive) setError(true)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  const rename = useCallback(async (id: string, title: string) => {
    // Optimistic: the rep sees the new title immediately, and a failed PATCH
    // is corrected by the next refresh rather than blocking the edit.
    setConversations((prev) => prev?.map((c) => (c.id === id ? { ...c, title } : c)))
    await api.renameConversation(id, title).catch(() => undefined)
  }, [])

  const remove = useCallback(async (id: string) => {
    setConversations((prev) => prev?.filter((c) => c.id !== id))
    await api.deleteConversation(id).catch(() => undefined)
  }, [])

  return { conversations, loading, error, refresh, rename, remove }
}
