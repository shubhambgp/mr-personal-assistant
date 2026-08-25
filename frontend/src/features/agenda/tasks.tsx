// The task manager: the add row, the filter bar and the task rows.
//
// Moved out of AgendaPanel.tsx purely for navigability — the panel file had
// grown to ~750 lines with two features in it. Zero behaviour change: these
// are the same components, and AgendaPanel still owns all state via useAgenda.

import { useState } from 'react'
import {
  CalendarCheck,
  CheckCircle2,
  Plus,
  RotateCcw,
  Star,
  StickyNote,
  Trash2,
} from 'lucide-react'

import { Badge, Button, IconButton } from '@/components/ui'
import { cx } from '@/lib/cx'
import { dueLabel } from '@/lib/format'
import type { AgendaTask, TaskFilters, TaskPatch } from '@/lib/types'

const FIELD =
  'min-w-0 rounded-lg border border-line bg-surface px-2 py-1.5 text-xs text-fg outline-none placeholder:text-fg-subtle focus:border-accent'

export function TaskFilterBar({
  filters,
  doctors,
  onChange,
}: {
  filters: TaskFilters
  doctors: [number, string][]
  onChange: (next: Partial<TaskFilters>) => void
}) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      {(['open', 'done', 'all'] as const)?.map((value) => (
        <Button
          key={value}
          variant={filters.status === value ? 'subtle' : 'ghost'}
          size="sm"
          onClick={() => onChange({ status: value })}
          aria-pressed={filters.status === value}
        >
          {value}
        </Button>
      ))}

      <span className="mx-0.5 h-4 w-px bg-line" aria-hidden="true" />

      <Button
        variant={filters.important ? 'subtle' : 'ghost'}
        size="sm"
        onClick={() => onChange({ important: !filters.important })}
        aria-pressed={filters.important}
      >
        <Star className={cx('size-3', filters.important && 'fill-current')} aria-hidden="true" />
        important
      </Button>

      <select
        value={filters.source ?? ''}
        onChange={(e) => onChange({ source: (e.target.value || null) as TaskFilters['source'] })}
        aria-label="Filter by who added the task"
        className={cx(FIELD, 'py-1')}
      >
        <option value="">anyone</option>
        <option value="rep">added by me</option>
        <option value="assistant">from chat</option>
      </select>

      {doctors.length > 0 && (
        <select
          value={filters.doctorId ?? ''}
          onChange={(e) => onChange({ doctorId: e.target.value ? Number(e.target.value) : null })}
          aria-label="Filter by doctor"
          className={cx(FIELD, 'py-1')}
        >
          <option value="">any doctor</option>
          {doctors?.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

export function AddTaskRow({
  onAdd,
}: {
  onAdd: (task: {
    title: string
    due_date?: string | null
    due_time?: string | null
    important?: boolean
    notes?: string | null
  }) => void | Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const [at, setAt] = useState('')
  const [important, setImportant] = useState(false)
  const [notes, setNotes] = useState('')
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    const trimmed = title.trim()
    if (!trimmed || saving) return
    setSaving(true)
    try {
      // A time with no date is refused by the server (and by the table), so it
      // is dropped here rather than sent to fail.
      await onAdd({
        title: trimmed,
        due_date: due || null,
        due_time: due && at ? at : null,
        important,
        notes: notes.trim() || null,
      })
    } catch {
      // The hook has surfaced the error in the panel banner; keep every typed
      // value so the rep can retry rather than re-typing a lost task.
      setSaving(false)
      return
    }
    // Clear ONLY after the task is safely saved.
    setTitle('')
    setDue('')
    setAt('')
    setImportant(false)
    setNotes('')
    setOpen(false)
    setSaving(false)
  }

  return (
    <div className="mb-3 flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-1.5">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) void submit()
          }}
          placeholder="Add a task…"
          aria-label="New task"
          className={cx(FIELD, 'flex-1 basis-40')}
        />
        <input
          type="date"
          value={due}
          onChange={(e) => setDue(e.target.value)}
          aria-label="Due date"
          className={cx(FIELD, 'shrink-0')}
        />
        <input
          type="time"
          value={at}
          onChange={(e) => setAt(e.target.value)}
          // A time is meaningless without a date, so the control says so rather
          // than accepting input the server will reject.
          disabled={!due}
          aria-label="Due time"
          title={due ? 'Optional time' : 'Pick a date first'}
          className={cx(FIELD, 'shrink-0 disabled:opacity-40')}
        />
        <IconButton
          label={important ? 'Not important' : 'Mark important'}
          size="sm"
          onClick={() => setImportant((v) => !v)}
        >
          <Star
            className={cx('size-4', important ? 'fill-current text-warning' : 'text-fg-subtle')}
            aria-hidden="true"
          />
        </IconButton>
        <Button
          variant="subtle"
          size="md"
          onClick={() => void submit()}
          disabled={!title.trim() || saving}
        >
          <Plus className="size-3.5" aria-hidden="true" />
          Add
        </Button>
      </div>
      {open ? (
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          placeholder="Notes…"
          aria-label="Notes"
          className={cx(FIELD, 'w-full resize-y')}
        />
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="self-start text-2xs text-fg-subtle underline-offset-2 hover:text-fg-muted hover:underline"
        >
          Add notes
        </button>
      )}
    </div>
  )
}

export function TaskRow({
  task,
  onDone,
  onDelete,
  onPatch,
}: {
  task: AgendaTask
  onDone: (done: boolean) => void
  onDelete: () => void
  onPatch: (patch: TaskPatch) => void
}) {
  const [editing, setEditing] = useState(false)
  const [showNotes, setShowNotes] = useState(false)
  const done = task.done_at !== null

  // Inline edit, built from ConversationRow's rename-input pattern rather than a
  // dialog: there is no Dialog primitive here, and the house style for a small
  // in-place change is an in-place swap.
  const [title, setTitle] = useState(task.title)
  const [due, setDue] = useState(task.due_date ?? '')
  const [at, setAt] = useState(task.due_time ?? '')
  const [notes, setNotes] = useState(task.notes ?? '')

  const save = () => {
    const trimmed = title.trim()
    if (!trimmed) return
    const patch: TaskPatch = {}
    if (trimmed !== task.title) patch.title = trimmed
    if ((due || null) !== task.due_date) patch.due_date = due || null
    // Clearing the date clears the time with it: the table refuses a time with
    // no date, and silently keeping one would fail the update.
    const nextTime = due ? at || null : null
    if (nextTime !== task.due_time) patch.due_time = nextTime
    if ((notes.trim() || null) !== task.notes) patch.notes = notes.trim() || null
    setEditing(false)
    if (Object.keys(patch).length > 0) onPatch(patch)
  }

  if (editing) {
    return (
      <li className="flex flex-col gap-1.5 rounded-card border border-accent/40 bg-surface px-3 py-2">
        <div className="flex flex-wrap gap-1.5">
          <input
            value={title}
            autoFocus
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) save()
              if (e.key === 'Escape') setEditing(false)
            }}
            aria-label="Task title"
            className={cx(FIELD, 'flex-1 basis-40')}
          />
          <input
            type="date"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            aria-label="Due date"
            className={cx(FIELD, 'shrink-0')}
          />
          <input
            type="time"
            value={at}
            onChange={(e) => setAt(e.target.value)}
            disabled={!due}
            aria-label="Due time"
            className={cx(FIELD, 'shrink-0 disabled:opacity-40')}
          />
        </div>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          placeholder="Notes…"
          aria-label="Notes"
          className={cx(FIELD, 'w-full resize-y')}
        />
        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="primary" size="sm" onClick={save} disabled={!title.trim()}>
            Save
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
            Cancel
          </Button>
          <Button variant="ghost" size="sm" onClick={() => onPatch({ important: !task.important })}>
            <Star
              className={cx('size-3', task.important && 'fill-current text-warning')}
              aria-hidden="true"
            />
            {task.important ? 'Not important' : 'Important'}
          </Button>
        </div>
      </li>
    )
  }

  return (
    <li className="group flex flex-col rounded-lg px-2 py-1.5 hover:bg-overlay/6">
      <div className="flex min-w-0 items-center gap-2">
        <IconButton label={done ? 'Reopen' : 'Mark done'} size="sm" onClick={() => onDone(!done)}>
          {done ? (
            <RotateCcw className="size-4" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="size-4" aria-hidden="true" />
          )}
        </IconButton>

        {task.important && (
          <Star className="size-3 shrink-0 fill-current text-warning" aria-label="Important" />
        )}

        <button
          type="button"
          onClick={() => setEditing(true)}
          className={cx(
            'min-w-0 flex-1 truncate text-left text-xs',
            done ? 'text-fg-subtle line-through' : 'text-fg',
          )}
          title="Edit"
        >
          {task.title}
        </button>

        {task.doctor_name && (
          <span className="hidden shrink-0 text-2xs text-fg-subtle sm:inline">
            {task.doctor_name}
          </span>
        )}
        {task.calendar_event_id && (
          <CalendarCheck className="size-3 shrink-0 text-fg-subtle" aria-label="On your calendar" />
        )}
        {task.source === 'assistant' && (
          /* Worth distinguishing: "the assistant thought I promised this" is a
             different kind of claim from "I wrote this down". */
          <Badge tone="neutral">from chat</Badge>
        )}
        {task.due_date && (
          <span className="shrink-0 text-2xs tabular-nums text-fg-subtle">
            {dueLabel(task.due_date, task.due_time)}
          </span>
        )}
        {task.notes && (
          <IconButton
            label={showNotes ? 'Hide notes' : 'Show notes'}
            size="sm"
            onClick={() => setShowNotes((v) => !v)}
          >
            <StickyNote className="size-3.5" aria-hidden="true" />
          </IconButton>
        )}
        <IconButton label="Delete task" variant="danger" size="sm" onClick={onDelete}>
          <Trash2 className="size-3.5" aria-hidden="true" />
        </IconButton>
      </div>

      {/* The notes column has existed since the table was created and had no way
          to reach the screen. This is it. */}
      {showNotes && task.notes && (
        <p className="mt-1 whitespace-pre-wrap pl-8 text-2xs text-fg-muted">{task.notes}</p>
      )}
    </li>
  )
}
