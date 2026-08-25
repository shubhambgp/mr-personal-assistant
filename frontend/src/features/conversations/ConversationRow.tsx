import { useEffect, useRef, useState } from 'react'
import { Check, MoreHorizontal, Pencil, Trash2, X } from 'lucide-react'

import { IconButton, Menu, MenuItem } from '@/components/ui'
import { cx } from '@/lib/cx'
import type { ConversationSummary } from '@/lib/types'

export function ConversationRow({
  conversation,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  conversation: ConversationSummary
  active: boolean
  onSelect: () => void
  onRename: (title: string) => void
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [dropUp, setDropUp] = useState(false)
  const [draft, setDraft] = useState(conversation.title ?? '')
  const inputRef = useRef<HTMLInputElement>(null)

  // Editing ends exactly once, and Escape must end it *without saving*.
  //
  // Two earlier versions of this got it wrong. The original committed on
  // Escape, because cancelling and saving were the same code path (blur fires
  // on unmount with onBlur still attached). The second routed Escape through
  // `e.currentTarget.blur()` to reach that path deliberately — which silently
  // does nothing whenever the input is not actually focused, leaving the row
  // stuck in edit mode. `finish(save)` is direct and focus-independent, and
  // the ref makes it idempotent so a trailing blur cannot double-commit.
  const finishedRef = useRef(false)

  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  const startEditing = () => {
    setDraft(conversation.title ?? '')
    finishedRef.current = false
    setMenuOpen(false)
    setEditing(true)
  }

  const finish = (save: boolean) => {
    if (finishedRef.current) return
    finishedRef.current = true
    if (save) {
      const next = draft.trim()
      if (next && next !== conversation.title) onRename(next)
    }
    setEditing(false)
  }

  if (editing) {
    return (
      <li>
        <input
          ref={inputRef}
          autoFocus
          value={draft}
          aria-label="Conversation title"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => finish(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              finish(true)
            }
            if (e.key === 'Escape') {
              e.preventDefault()
              finish(false)
            }
          }}
          className="w-full rounded-lg border border-accent bg-surface px-2 py-1.5 text-xs text-fg outline-none"
        />
      </li>
    )
  }

  if (confirming) {
    return (
      <li className="flex items-center gap-1 rounded-lg bg-danger/12 px-2 py-1">
        <span className="min-w-0 flex-1 truncate text-2xs text-danger">Delete this chat?</span>
        <IconButton label="Confirm delete" variant="danger" size="sm" onClick={onDelete}>
          <Check className="size-3.5" aria-hidden="true" />
        </IconButton>
        <IconButton label="Cancel" size="sm" onClick={() => setConfirming(false)}>
          <X className="size-3.5" aria-hidden="true" />
        </IconButton>
      </li>
    )
  }

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onSelect}
        onDoubleClick={startEditing}
        onKeyDown={(e) => {
          if (e.key === 'F2') {
            e.preventDefault()
            startEditing()
          }
        }}
        title={conversation.title ?? 'Untitled'}
        aria-current={active ? 'page' : undefined}
        className={cx(
          'relative w-full truncate rounded-lg py-1.5 pl-3 pr-8 text-left text-xs transition-colors',
          active
            ? 'bg-accent-soft font-medium text-accent-ink'
            : 'text-fg-muted hover:bg-overlay/6 hover:text-fg',
        )}
      >
        {/* A 2px rail growing from zero height: says where you are without a
            heavy fill. */}
        {active && (
          <span
            aria-hidden="true"
            className="animate-rail absolute inset-y-1 left-0 w-0.5 origin-top rounded-full bg-accent"
          />
        )}
        {conversation.title || 'Untitled'}
      </button>

      {/* focus-within as well as hover: with hover alone this control was
          literally unreachable by keyboard. */}
      <div
        className={cx(
          'absolute right-0.5 top-1/2 -translate-y-1/2 transition-opacity',
          'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',
          /* z-30 while open is load-bearing, not belt-and-braces. This wrapper's
             transform creates a stacking context that traps the Menu's own
             z-index INSIDE it, and the wrapper itself sat at z-auto — so the
             later sibling rows (each `li.relative`, painted in DOM order)
             rendered their transparent full-width buttons OVER the open menu:
             visible, but every click landed on the row behind it. The parent li
             is z-auto (no stacking context), so this z escapes to the drawer
             panel's context and beats every later row. */
          menuOpen && 'z-30 opacity-100',
        )}
      >
        <IconButton
          label="Conversation options"
          size="sm"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={(e) => {
            // The list is a scroll container (overflow-y-auto), which CLIPS an
            // absolutely-positioned menu — no z-index can fix clipping. For the
            // last rows there is not enough room below the trigger, so the menu
            // opens upward instead.
            const scroller = e.currentTarget.closest('nav')
            const rect = e.currentTarget.getBoundingClientRect()
            const bounds = scroller?.getBoundingClientRect()
            setDropUp(bounds !== undefined && bounds.bottom - rect.bottom < 96)
            setMenuOpen((v) => !v)
          }}
        >
          <MoreHorizontal className="size-3.5" aria-hidden="true" />
        </IconButton>

        <Menu open={menuOpen} side={dropUp ? 'up' : 'down'} onClose={() => setMenuOpen(false)}>
          <MenuItem
            onClick={startEditing}
            icon={<Pencil className="size-3.5" aria-hidden="true" />}
          >
            Rename
          </MenuItem>
          <MenuItem
            tone="danger"
            onClick={() => {
              setMenuOpen(false)
              setConfirming(true)
            }}
            icon={<Trash2 className="size-3.5" aria-hidden="true" />}
          >
            Delete
          </MenuItem>
        </Menu>
      </div>
    </li>
  )
}
