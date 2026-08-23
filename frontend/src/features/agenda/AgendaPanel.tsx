// The rep's morning: mail that needs them, what is on the calendar, and what
// they wrote down.
//
// Fed by GET /api/agenda, which calls the SAME service functions the chat tools
// call — so the panel and the assistant can never disagree about what needs
// attention. No model runs behind this, which is why it is fast and free.
//
// Not a Table: the triage list is a list of actions, each with a reason and a
// next step. ToolResult's Table infers columns and truncates cells, which is the
// right affordance for a query result and the wrong one here.

import {
  CalendarCheck,
  CalendarDays,
  CheckCircle2,
  Inbox,
  ListTodo,
  Plug,
  Plus,
  RefreshCw,
  RotateCcw,
  Star,
  StickyNote,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'

import { Badge, Button, Card, IconButton, Skeleton } from '@/components/ui'
import { cx } from '@/lib/cx'
import { CONTENT_COL, dueLabel } from '@/lib/format'
import type {
  AgendaTask,
  CalendarEntry,
  TaskFilters,
  TaskPatch,
  TaskSection,
  TriageItem,
} from '@/lib/types'

import { useAgenda } from './useAgenda'

/** Category -> how it reads to a rep. The backend computes the category from
 *  thread structure; this only names it. */
interface CategoryMeta {
  label: string
  tone: 'danger' | 'warning' | 'accent' | 'neutral'
}

/** The fallback is a real constant, not CATEGORY.fyi: `noUncheckedIndexedAccess`
 *  makes every lookup possibly-undefined, so an indexed default is no default. */
const FYI: CategoryMeta = { label: 'fyi', tone: 'neutral' }

const CATEGORY: Record<string, CategoryMeta> = {
  escalate: { label: 'escalate', tone: 'danger' },
  needs_reply: { label: 'needs a reply', tone: 'warning' },
  follow_up_due: { label: 'follow up', tone: 'accent' },
  awaiting_reply: { label: 'waiting', tone: 'neutral' },
  fyi: FYI,
}

const ACTIONABLE = ['escalate', 'needs_reply', 'follow_up_due']

/** "Today" or a short weekday. Calendar entries run forwards, so format.ts's
 *  bucketFor — which buckets conversations backwards into Yesterday/Older — is
 *  the wrong tool here. */
function dayLabel(iso: string): string {
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return ''
  const today = new Date()
  if (when.toDateString() === today.toDateString()) return 'today'
  return when.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' })
}

export function AgendaPanel({
  active,
  onAsk,
  onOpenSettings,
}: {
  active: boolean
  /** Sends a prompt into the chat. The panel is a launcher, not a dead end. */
  onAsk: (prompt: string) => void
  onOpenSettings: () => void
}) {
  const {
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
  } = useAgenda(active)

  // Mail category tabs. Client-side on purpose: triage already returns the
  // category, so this is a pick over data already in hand — no refetch, and no
  // Gmail round trip for changing a tab.
  const [mailTab, setMailTab] = useState<'actionable' | 'all'>('actionable')

  return (
    /* min-h-0 and min-w-0 are load-bearing on a grid item and have no visual
       signature until something wide arrives — see ENGINEERING_LOG 11. */
    <div className="scrollbar-thin min-h-0 min-w-0 overflow-y-auto px-4 py-5 sm:px-6">
      <div className={cx(CONTENT_COL, 'flex flex-col gap-5')}>
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="font-serif text-xl text-fg">Your agenda</h2>
            {agenda && (
              <p className="text-2xs text-fg-subtle">
                {agenda.connected
                  ? `Mail and calendar as of ${agenda.as_of}.`
                  : agenda.mail_state === 'stale'
                    ? 'Tasks only — your mailbox connection expired.'
                    : 'Tasks only — no mailbox connected.'}
              </p>
            )}
          </div>
          <IconButton label="Refresh" onClick={() => void reload()} disabled={loading}>
            <RefreshCw className={cx('size-4', loading && 'animate-spin')} aria-hidden="true" />
          </IconButton>
        </header>

        {error && (
          <p role="alert" className="rounded-card bg-danger/12 px-3 py-2 text-2xs text-danger">{error}</p>
        )}

        {loading && !agenda && (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        )}

        {agenda && !agenda.connected && (
          <Card className="flex flex-col gap-2 p-4">
            <div className="flex items-center gap-2">
              <Plug className="size-4 text-fg-muted" aria-hidden="true" />
              <h2 className="text-sm font-medium text-fg">
                {agenda.mail_state === 'stale'
                  ? 'Reconnect Gmail and Calendar'
                  : 'Connect Gmail and Calendar'}
              </h2>
            </div>
            <p className="text-2xs text-fg-muted">
              {!agenda.configured
                ? 'This server has no Google client configured, so mail and calendar are unavailable. Your tasks below still work.'
                : agenda.mail_state === 'stale'
                  ? // "Not connected" would be a lie to a rep who did connect,
                    // and would send them to redo work they already did.
                    'Your Google connection has expired, so mail and calendar are unavailable until you reconnect. It takes one click.'
                  : 'Connect your Google account to see which mail needs you, what is on your calendar, and to draft replies here. Nothing is sent without your approval.'}
            </p>
            {agenda.configured && (
              <div>
                <Button variant="primary" size="md" onClick={onOpenSettings}>
                  {agenda.mail_state === 'stale' ? 'Reconnect in Settings' : 'Open Settings'}
                </Button>
              </div>
            )}
          </Card>
        )}

        {agenda?.mail_error && (
          <p className="rounded-card bg-warning/12 px-3 py-2 text-2xs text-warning">
            Could not read your mailbox: {agenda.mail_error}
          </p>
        )}

        {agenda && agenda.mail.length > 0 && (
          <Section icon={Inbox} title="Mail" count={agenda.counts.needs_reply}>
            {/* Tabs rather than a filter bar: triage already returned the
                category, so switching is a pick over data in hand. No refetch,
                and no Gmail round trip for a tab. */}
            <div className="mb-1 flex flex-wrap gap-1.5">
              {(
                [
                  ['actionable', 'Needs you'],
                  ['all', 'All'],
                ] as const
              ).map(([key, label]) => (
                <Button
                  key={key}
                  variant={mailTab === key ? 'subtle' : 'ghost'}
                  size="sm"
                  onClick={() => setMailTab(key)}
                  aria-pressed={mailTab === key}
                >
                  {label}
                </Button>
              ))}
            </div>
            <ul className="flex flex-col gap-1.5">
              {agenda.mail
                .filter((m) => mailTab === 'all' || ACTIONABLE.includes(m.category))
                .map((item) => (
                  <MailRow key={item.thread_id} item={item} onAsk={onAsk} />
                ))}
            </ul>
            {mailTab === 'actionable' &&
              agenda.mail.filter((m) => ACTIONABLE.includes(m.category)).length === 0 && (
                <p className="text-2xs text-fg-subtle">
                  Nothing needs a reply. Switch to All to see the rest.
                </p>
              )}
          </Section>
        )}

        {agenda && agenda.calendar.length > 0 && (
          <Section icon={CalendarDays} title="Calendar" count={agenda.counts.events_today}>
            <ul className="flex flex-col gap-1.5">
              {agenda.calendar.map((event) => (
                <EventRow key={event.event_id} event={event} />
              ))}
            </ul>
          </Section>
        )}

        <Section
          icon={ListTodo}
          title="Your tasks"
          count={tasks?.row_count}
          /* Overdue is the number that should make a rep act, so it sits in the
             header rather than needing a scroll to find. The count comes from the
             server with the rows it describes — never counted here. */
          badge={
            tasks && tasks.counts.overdue > 0 ? (
              <Badge tone="danger">{tasks.counts.overdue} overdue</Badge>
            ) : null
          }
        >
          <AddTaskRow onAdd={(task) => void addTask(task)} />

          <TaskFilterBar
            filters={filters}
            doctors={tasks?.doctors ?? []}
            onChange={applyFilters}
          />

          {tasks && tasks.rows.length > 0 ? (
            <div className="flex flex-col gap-3">
              {SECTIONS.map(({ key, label, tone }) => {
                const rows = tasks.rows.filter((t) => t.section === key)
                if (rows.length === 0) return null
                return (
                  <div key={key} className="flex flex-col gap-1">
                    <div className="flex items-center gap-1.5">
                      <Badge tone={tone}>{label}</Badge>
                      <span className="text-2xs tabular-nums text-fg-subtle">{rows.length}</span>
                    </div>
                    <ul className="flex flex-col gap-1">
                      {rows.map((task) => (
                        <TaskRow
                          key={task.id}
                          task={task}
                          onDone={(done) => void completeTask(task.id, done)}
                          onDelete={() => void deleteTask(task.id)}
                          onPatch={(patch) => void patchTask(task.id, patch)}
                        />
                      ))}
                    </ul>
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-2xs text-fg-subtle">
              {filters.status !== 'open' || filters.important || filters.source || filters.doctorId
                ? 'Nothing matches these filters.'
                : 'Nothing yet. You can also just tell the assistant — “remind me to send Dr Sharma the dosing card on Friday”.'}
            </p>
          )}
        </Section>
      </div>
    </div>
  )
}

function Section({
  icon: Icon,
  title,
  count,
  badge,
  children,
}: {
  icon: typeof Inbox
  title: string
  count?: number
  /** A slot on the right of the heading, for the one number that should make a
   *  rep act before they scroll. */
  badge?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="flex flex-wrap items-center gap-1.5 text-label uppercase text-fg-subtle">
        <Icon className="size-3.5" aria-hidden="true" />
        {title}
        {typeof count === 'number' && count > 0 && (
          <span className="text-fg-muted">· {count}</span>
        )}
        {badge}
      </h2>
      {children}
    </section>
  )
}

function MailRow({ item, onAsk }: { item: TriageItem; onAsk: (prompt: string) => void }) {
  const meta = CATEGORY[item.category] ?? FYI
  return (
    <li className="rounded-card border border-line bg-surface px-3 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <span className="min-w-0 truncate text-xs font-medium text-fg">{item.from_name}</span>
        {item.doctor_name && (
          <span className="truncate text-2xs text-fg-subtle">· in your book</span>
        )}
        <span className="ml-auto shrink-0 text-2xs tabular-nums text-fg-subtle">
          {item.days_waiting}d
        </span>
      </div>
      <p className="mt-0.5 truncate text-xs text-fg-muted">{item.subject}</p>
      {/* The reason the category was assigned, computed server-side. */}
      <p className="mt-0.5 text-2xs text-fg-subtle">{item.reason}</p>
      <div className="mt-1.5 flex gap-1.5">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onAsk(`Summarise the mail thread from ${item.from_name} about "${item.subject}" and tell me what it needs.`)}
        >
          Summarise
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onAsk(`Draft a reply to ${item.from_name} about "${item.subject}".`)}
        >
          Draft a reply
        </Button>
      </div>
    </li>
  )
}

function EventRow({ event }: { event: CalendarEntry }) {
  return (
    <li className="flex min-w-0 items-baseline gap-2 rounded-card border border-line bg-surface px-3 py-2">
      <span className="shrink-0 text-2xs tabular-nums text-fg-muted">
        {event.all_day ? 'all day' : new Date(event.start).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-fg">{event.title}</span>
      <span className="shrink-0 text-2xs text-fg-subtle">{dayLabel(event.start)}</span>
    </li>
  )
}

/** The five task sections, in the order a rep reads them, with how each reads.
 *
 *  `important` is deliberately NOT a section: a task that is both important and
 *  overdue is genuinely both, and one row in two places makes every count a
 *  half-truth. It sorts to the top of its own section instead, with a marker. */
const SECTIONS: readonly { key: TaskSection; label: string; tone: CategoryMeta['tone'] }[] = [
  { key: 'overdue', label: 'Overdue', tone: 'danger' },
  { key: 'today', label: 'Today', tone: 'warning' },
  { key: 'upcoming', label: 'Upcoming', tone: 'accent' },
  { key: 'someday', label: 'No date', tone: 'neutral' },
  { key: 'done', label: 'Done', tone: 'neutral' },
]

const FIELD =
  'min-w-0 rounded-lg border border-line bg-surface px-2 py-1.5 text-xs text-fg outline-none placeholder:text-fg-subtle focus:border-accent'

function TaskFilterBar({
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
      {(['open', 'done', 'all'] as const).map((value) => (
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
        <Star
          className={cx('size-3', filters.important && 'fill-current')}
          aria-hidden="true"
        />
        important
      </Button>

      <select
        value={filters.source ?? ''}
        onChange={(e) =>
          onChange({ source: (e.target.value || null) as TaskFilters['source'] })
        }
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
          onChange={(e) =>
            onChange({ doctorId: e.target.value ? Number(e.target.value) : null })
          }
          aria-label="Filter by doctor"
          className={cx(FIELD, 'py-1')}
        >
          <option value="">any doctor</option>
          {doctors.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

function AddTaskRow({
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

function TaskRow({
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
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onPatch({ important: !task.important })}
          >
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
        <IconButton
          label={done ? 'Reopen' : 'Mark done'}
          size="sm"
          onClick={() => onDone(!done)}
        >
          {done ? (
            <RotateCcw className="size-4" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="size-4" aria-hidden="true" />
          )}
        </IconButton>

        {task.important && (
          <Star
            className="size-3 shrink-0 fill-current text-warning"
            aria-label="Important"
          />
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
          <CalendarCheck
            className="size-3 shrink-0 text-fg-subtle"
            aria-label="On your calendar"
          />
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
