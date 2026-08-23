// The overdue number on the sidebar's Agenda entry.
//
// A separate, tiny fetch rather than lifting state out of useAgenda, because the
// badge's whole purpose is to be visible FROM THE CHAT VIEW — where AgendaPanel
// is not mounted and its hook is not running.
//
// It asks for `limit=1`: only `counts` is used, and the server computes those
// over the filtered set, so no rows need to come back.

import { useCallback, useEffect, useState } from 'react'

import { api } from '@/lib/api'

export function useOverdueCount(refreshKey: unknown) {
  const [overdue, setOverdue] = useState<number | undefined>(undefined)

  const load = useCallback(async (isCancelled: () => boolean) => {
    try {
      const list = await api.tasks({
        status: 'open',
        important: false,
        source: null,
        doctorId: null,
      })
      // Checked AFTER the await: the old guard ran before it, so a response
      // landing post-cleanup still wrote state for an unmounted consumer.
      if (!isCancelled()) setOverdue(list.counts.overdue)
    } catch {
      // A missing badge is the right failure: nothing about the count is worth
      // an error banner over the chat.
      if (!isCancelled()) setOverdue(undefined)
    }
  }, [])

  // Re-read whenever the caller's key changes — App passes the current view, so
  // leaving the Agenda panel after editing tasks refreshes the badge.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      await load(() => cancelled)
    })()
    return () => {
      cancelled = true
    }
  }, [load, refreshKey])

  return overdue
}
