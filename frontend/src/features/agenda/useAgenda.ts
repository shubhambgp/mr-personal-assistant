// The Agenda panel's data, and the task mutations.
//
// A hook in its own .ts file, per the convention here: a module that exports
// both a component and a non-component breaks react-refresh.
//
// TWO fetches, not one, and the split is deliberate. `GET /api/agenda` is the
// at-a-glance view — mail, calendar and the counts — and costs Gmail round
// trips. `GET /api/agenda/tasks` is the task browser and takes the filters. So
// changing a filter re-fetches tasks alone and never touches the mailbox.

import { useCallback, useEffect, useState } from 'react'

import { api, ApiError } from '@/lib/api'
import type { Agenda, TaskFilters, TaskList, TaskPatch } from '@/lib/types'

export const DEFAULT_FILTERS: TaskFilters = {
  status: 'open',
  important: false,
  source: null,
  doctorId: null,
}

export function useAgenda(active: boolean) {
  const [agenda, setAgenda] = useState<Agenda | null>(null)
  const [tasks, setTasks] = useState<TaskList | null>(null)
  const [filters, setFilters] = useState<TaskFilters>(DEFAULT_FILTERS)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadTasks = useCallback(async (next: TaskFilters) => {
    setTasks(await api.tasks(next))
  }, [])

  /** Manual refresh, from the button. Safe to set state synchronously here
   *  because it runs from an event handler, not from an effect. */
  const reload = useCallback(async () => {
    setRefreshing(true)
    setError(null)
    try {
      const [next, list] = await Promise.all([api.agenda(), api.tasks(filters)])
      setAgenda(next)
      setTasks(list)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load your agenda.')
    } finally {
      setRefreshing(false)
    }
  }, [filters])

  // Only fetched while the panel is actually open: it costs Gmail and Calendar
  // round trips, and the chat view never renders it.
  //
  // The fetch is inlined rather than calling `reload` because `reload` sets
  // state synchronously, which react-hooks/set-state-in-effect rightly refuses.
  // The `cancelled` flag is the real benefit: switching views mid-request no
  // longer writes state for a panel that has gone away.
  useEffect(() => {
    if (!active) return
    let cancelled = false
    void (async () => {
      try {
        const [next, list] = await Promise.all([api.agenda(), api.tasks(filters)])
        if (!cancelled) {
          setAgenda(next)
          setTasks(list)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load your agenda.')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [active, filters])

  const load = reload

  /** Applying a filter re-fetches TASKS ONLY. The effect above re-runs on the
   *  new `filters`, which also refreshes the mail counts — acceptable because a
   *  filter change is a deliberate act, and it keeps one code path. */
  const applyFilters = useCallback((next: Partial<TaskFilters>) => {
    setFilters((prev) => ({ ...prev, ...next }))
  }, [])

  const addTask = useCallback(
    async (task: {
      title: string
      due_date?: string | null
      due_time?: string | null
      important?: boolean
      notes?: string | null
    }) => {
      // try/catch AND re-throw: without it a failed POST on a flaky field
      // connection was an unhandled rejection with no UI feedback, and the row
      // that cleared its inputs before this resolved was silently lost. The
      // banner reports it; the throw lets the caller keep what the rep typed.
      // See audit finding M-FE2.
      try {
        await api.addTask(task)
        // Re-fetched rather than prepended: the server assigns the section, and a
        // locally-inserted row would have to guess it — which is the one thing the
        // browser is not allowed to decide.
        await loadTasks(filters)
        setError(null)
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not add the task.')
        throw err
      }
    },
    [filters, loadTasks],
  )

  const patchTask = useCallback(
    async (id: string, patch: TaskPatch) => {
      try {
        await api.patchTask(id, patch)
        await loadTasks(filters)
        setError(null)
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not update the task.')
        throw err
      }
    },
    [filters, loadTasks],
  )

  const completeTask = useCallback(
    async (id: string, done = true) => {
      // Optimistic: ticking a box should feel instant, and the failure path just
      // reloads the truth.
      setTasks((prev) =>
        prev ? { ...prev, rows: prev.rows.filter((t) => t.id !== id) } : prev,
      )
      try {
        await api.setTaskDone(id, done)
        await loadTasks(filters)
      } catch {
        void load()
      }
    },
    [filters, load, loadTasks],
  )

  const deleteTask = useCallback(
    async (id: string) => {
      setTasks((prev) =>
        prev ? { ...prev, rows: prev.rows.filter((t) => t.id !== id) } : prev,
      )
      try {
        await api.deleteTask(id)
        await loadTasks(filters)
      } catch {
        void load()
      }
    },
    [filters, load, loadTasks],
  )

  // `loading` is derived rather than tracked: the first paint has no agenda
  // and no error yet, which is exactly the skeleton state.
  const loading = refreshing || (agenda === null && error === null)

  return {
    agenda,
    tasks,
    filters,
    applyFilters,
    loading,
    error,
    reload,
    addTask,
    patchTask,
    completeTask,
    deleteTask,
  }
}
