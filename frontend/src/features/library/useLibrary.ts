// The Library page's data. A hook in its own .ts file, per the convention here:
// a module exporting both a component and a non-component breaks react-refresh.

import { useCallback, useEffect, useState } from 'react'

import { api, ApiError } from '@/lib/api'
import type { LibraryDocument } from '@/lib/api'

export function useLibrary(active: boolean) {
  const [documents, setDocuments] = useState<LibraryDocument[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Only fetched while the panel is actually open — the list endpoint scrolls
  // the whole vector collection, and the chat view never renders it.
  useEffect(() => {
    if (!active) return
    let cancelled = false
    void (async () => {
      try {
        const docs = await api.documents()
        if (!cancelled) {
          setDocuments(docs)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load your library.')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [active])

  /** Manual refresh — safe to set state synchronously from an event handler. */
  const refresh = useCallback(async () => {
    try {
      setDocuments(await api.documents())
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load your library.')
    }
  }, [])

  // Derived, not tracked: the first paint has no documents and no error yet,
  // which is exactly the skeleton state.
  const loading = documents === null && error === null

  return { documents: documents ?? [], loading, error, refresh }
}
