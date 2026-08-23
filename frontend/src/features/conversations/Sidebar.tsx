// Conversation history. Every list here is already scoped to the signed-in rep
// by the API — the client never sends a chair_id.

import { useMemo, useState } from 'react'
import {
  CalendarCheck,
  LibraryBig,
  LogOut,
  Mail,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  X,
} from 'lucide-react'

import { ThemeToggle } from '@/app/layout/ThemeToggle'
import { Badge, Button, IconButton, Menu, MenuItem, Skeleton } from '@/components/ui'
import { bucketFor, DATE_BUCKETS } from '@/lib/format'
import type { DateBucket } from '@/lib/format'
import type { ConversationSummary } from '@/lib/types'
import { cx } from '@/lib/cx'

import { ConversationRow } from './ConversationRow'

type View = 'chat' | 'agenda' | 'settings' | 'library'

interface Props {
  conversations: ConversationSummary[]
  activeId: string | null
  loading: boolean
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
  /** Which pane is showing. The app has no router: this swaps the middle grid
   *  row in App.tsx. */
  view: View
  onView: (view: View) => void
  /** Overdue tasks, so a rep sees the one number that should make them act
   *  without opening the panel. Undefined until it is known — 0 and "not loaded
   *  yet" must not render the same, or an empty badge flashes on every load. */
  overdueCount?: number
  repName: string
  repCode: number
  onLogout: () => void
  /** Desktop-only icon rail. App ANDs this with the desktop media query, so the
   *  mobile drawer always renders the full sidebar. */
  collapsed?: boolean
  onToggleCollapse?: () => void
  /** The initial list fetch failed. Rendered as its own state — an offline rep
   *  must not be told "No conversations yet", which the app cannot know. */
  listError?: boolean
  onRetryList?: () => void
}

export function Sidebar({
  conversations,
  activeId,
  loading,
  onSelect,
  onNew,
  onDelete,
  onRename,
  view,
  onView,
  overdueCount,
  repName,
  repCode,
  onLogout,
  collapsed = false,
  onToggleCollapse,
  listError = false,
  onRetryList,
}: Props) {
  const [query, setQuery] = useState('')

  // Title-only search, filtered locally: the list is already in memory and
  // capped at 50 by the API, so a server round trip would buy nothing.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return conversations
    return conversations.filter((c) => (c.title ?? 'Untitled').toLowerCase().includes(q))
  }, [conversations, query])

  // Grouped by `updated_at`, which the API already returned and nothing
  // rendered. A flat list of 40 titles gives the reader no way in.
  const grouped = useMemo(() => {
    const map = new Map<DateBucket, ConversationSummary[]>()
    for (const c of filtered) {
      const bucket = bucketFor(c.updated_at)
      const list = map.get(bucket)
      if (list) list.push(c)
      else map.set(bucket, [c])
    }
    return DATE_BUCKETS.flatMap((bucket) => {
      const items = map.get(bucket)
      return items?.length ? [{ bucket, items }] : []
    })
  }, [filtered])

  const openConversation = (id: string) => {
    onSelect(id)
    // Landing in the conversation with the full list restored beats keeping a
    // filter the rep has already acted on.
    setQuery('')
  }

  if (collapsed) {
    return (
      <div className="flex h-full min-h-0 flex-col items-center gap-1.5 p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        <IconButton label="Expand sidebar" onClick={onToggleCollapse}>
          <PanelLeftOpen className="size-4" aria-hidden="true" />
        </IconButton>
        <IconButton label="New chat" onClick={onNew}>
          <MessageSquarePlus className="size-4" aria-hidden="true" />
        </IconButton>
        <IconButton
          label={
            typeof overdueCount === 'number' && overdueCount > 0
              ? `Agenda — ${overdueCount} overdue`
              : 'Agenda'
          }
          variant={view === 'agenda' ? 'subtle' : 'ghost'}
          onClick={() => onView('agenda')}
          className="relative"
        >
          <CalendarCheck className="size-4" aria-hidden="true" />
          {/* No room for the count badge on a 56px rail; a dot says "something
              is overdue" and the label carries the number for screen readers. */}
          {typeof overdueCount === 'number' && overdueCount > 0 && (
            <span
              aria-hidden="true"
              className="absolute right-1 top-1 size-1.5 rounded-full bg-danger"
            />
          )}
        </IconButton>
        <IconButton
          label="Library"
          variant={view === 'library' ? 'subtle' : 'ghost'}
          onClick={() => onView('library')}
        >
          <LibraryBig className="size-4" aria-hidden="true" />
        </IconButton>

        <div className="flex-1" />

        <SettingsMenu compact onView={onView} onLogout={onLogout} />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
      <div className="flex items-center gap-1.5">
        <Button
          variant="outline"
          size="lg"
          onClick={onNew}
          className="min-w-0 flex-1 justify-start"
        >
          <MessageSquarePlus className="size-4" aria-hidden="true" />
          New chat
        </Button>
        {onToggleCollapse && (
          <IconButton
            label="Collapse sidebar"
            onClick={onToggleCollapse}
            className="hidden lg:inline-flex"
          >
            <PanelLeftClose className="size-4" aria-hidden="true" />
          </IconButton>
        )}
      </div>

      <Button
        variant={view === 'agenda' ? 'subtle' : 'ghost'}
        size="lg"
        onClick={() => onView('agenda')}
        aria-current={view === 'agenda' ? 'page' : undefined}
        className="w-full justify-start"
      >
        <CalendarCheck className="size-4" aria-hidden="true" />
        <span className="min-w-0 flex-1 text-left">Agenda</span>
        {typeof overdueCount === 'number' && overdueCount > 0 && (
          <Badge tone="danger" aria-label={`${overdueCount} overdue`}>
            {overdueCount}
          </Badge>
        )}
      </Button>

      <Button
        variant={view === 'library' ? 'subtle' : 'ghost'}
        size="lg"
        onClick={() => onView('library')}
        aria-current={view === 'library' ? 'page' : undefined}
        className="w-full justify-start"
      >
        <LibraryBig className="size-4" aria-hidden="true" />
        Library
      </Button>

      <div className="relative">
        <Search
          className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-fg-subtle"
          aria-hidden="true"
        />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search conversations…"
          aria-label="Search conversations"
          className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-8 text-xs text-fg outline-none transition-colors focus:border-accent"
        />
        {query && (
          <IconButton
            label="Clear search"
            size="sm"
            onClick={() => setQuery('')}
            className="absolute right-0.5 top-1/2 -translate-y-1/2"
          >
            <X className="size-3.5" aria-hidden="true" />
          </IconButton>
        )}
      </div>

      <nav aria-label="Conversation history" className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="space-y-1.5 p-1">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : listError && conversations.length === 0 ? (
          <div role="alert" className="flex flex-col items-center gap-2 px-3 py-6 text-center">
            <p className="text-2xs text-fg-subtle">Could not load your conversations.</p>
            {onRetryList && (
              <Button variant="outline" size="sm" onClick={onRetryList}>
                Retry
              </Button>
            )}
          </div>
        ) : conversations.length === 0 ? (
          <p className="px-3 py-6 text-center text-2xs text-fg-subtle">
            No conversations yet. Ask something to start one.
          </p>
        ) : filtered.length === 0 ? (
          <p role="status" className="px-3 py-6 text-center text-2xs text-fg-subtle">
            No conversations match.
          </p>
        ) : (
          grouped.map(({ bucket, items }) => (
            <section key={bucket} className="mb-2">
              <h2 className="px-3 pb-1 pt-2 text-label uppercase text-fg-subtle">{bucket}</h2>
              <ul className="space-y-0.5">
                {items.map((c) => (
                  <ConversationRow
                    key={c.id}
                    conversation={c}
                    active={activeId === c.id}
                    onSelect={() => openConversation(c.id)}
                    onRename={(title) => onRename(c.id, title)}
                    onDelete={() => onDelete(c.id)}
                  />
                ))}
              </ul>
            </section>
          ))
        )}
      </nav>

      <div className="border-t border-line pt-2">
        {/* min-w-0 via truncate: a long rep name must not widen the sidebar. */}
        <p className="truncate px-3 pb-1 text-2xs text-fg-subtle">
          {repName} · {repCode}
        </p>
        <SettingsMenu onView={onView} onLogout={onLogout} />
      </div>
    </div>
  )
}

/** The one Settings control: a drop-up with the theme toggle, the Gmail
 *  connection entry point, and sign-out. Shared between the full sidebar and
 *  the collapsed rail (`compact`), so the two can never drift apart. */
function SettingsMenu({
  compact = false,
  onView,
  onLogout,
}: {
  compact?: boolean
  onView: (view: View) => void
  onLogout: () => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className={cx('relative', !compact && 'w-full')}>
      {compact ? (
        <IconButton
          label="Settings"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <Settings className="size-4" aria-hidden="true" />
        </IconButton>
      ) : (
        <Button
          variant="ghost"
          size="md"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="w-full justify-start"
        >
          <Settings className="size-4" aria-hidden="true" />
          Settings
        </Button>
      )}

      {/* side="up": the trigger sits at the very bottom of the sidebar, so a
          downward menu would render off-screen. align="left" also makes the
          rail's menu open rightward, clear of the 56px column. */}
      <Menu open={open} onClose={() => setOpen(false)} side="up" align="left" className="w-56">
        {/* An embedded control row, not a menuitem: the toggle is operated in
            place and must not close the menu (the Menu's outside-pointerdown
            check already keeps clicks inside open). */}
        <div className="flex items-center justify-between gap-2 px-2 py-1.5">
          <span className="text-label uppercase text-fg-subtle">Theme</span>
          <ThemeToggle />
        </div>
        <div className="my-1 border-t border-line" aria-hidden="true" />
        <MenuItem
          icon={<Mail className="size-3.5" aria-hidden="true" />}
          onClick={() => {
            setOpen(false)
            // The Settings view, not a direct Google redirect: that page is
            // where connected/expired state and the granted scopes are
            // explained before anyone consents to anything.
            onView('settings')
          }}
        >
          Connect Gmail
        </MenuItem>
        <MenuItem
          tone="danger"
          icon={<LogOut className="size-3.5" aria-hidden="true" />}
          onClick={onLogout}
        >
          Sign out
        </MenuItem>
      </Menu>
    </div>
  )
}
