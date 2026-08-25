// A small popover menu. Not a dependency: what a library buys here is
// collision detection and portalling, neither of which this one-per-row menu
// needs — and it would arrive with its own theming layer.

import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

import { cx } from '@/lib/cx'

export function Menu({
  open,
  onClose,
  align = 'right',
  side = 'down',
  children,
  className,
}: {
  open: boolean
  onClose: () => void
  align?: 'left' | 'right'
  /** 'up' opens the menu above its trigger — for triggers near the bottom of a
   *  scroll container (last conversation rows) or at the bottom of the sidebar
   *  (the Settings drop-up), where opening downward would be clipped. */
  side?: 'down' | 'up'
  children: ReactNode
  className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  // Where focus goes back when the menu closes — captured at open, the same
  // pattern Drawer.tsx uses. Without it, Escape strands focus on <body>.
  const returnFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return

    returnFocusRef.current = document.activeElement as HTMLElement | null
    // The ARIA menu pattern: focus moves INTO the menu on open. Only
    // role="menuitem" participates — an embedded control row (the Settings
    // menu's Theme toggle) is deliberately not a menuitem, so arrow keys skip
    // it while Tab still reaches it.
    const items = () => [...(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])]
    items()[0]?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
      const list = items()
      if (!list.length) return
      e.preventDefault()
      const current = list.indexOf(document.activeElement as HTMLElement)
      const next =
        e.key === 'Home'
          ? 0
          : e.key === 'End'
            ? list.length - 1
            : e.key === 'ArrowDown'
              ? (current + 1) % list.length // wraps, per the ARIA pattern
              : (current - 1 + list.length) % list.length
      list[next]?.focus()
    }
    const onPointer = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    document.addEventListener('keydown', onKey)
    // `pointerdown` rather than `click`: a click listener added during the same
    // click that opened the menu fires immediately and closes it again.
    document.addEventListener('pointerdown', onPointer)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointer)
      returnFocusRef.current?.focus()
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      ref={ref}
      /* role="menu" now keeps its promise: focus moves in on open, ArrowUp/Down
         cycle (wrapping), Home/End jump, Escape closes and returns focus to the
         trigger. This closed audit finding M-FE7. */
      role="menu"
      /* Scales in from the corner it is anchored to, so the menu visibly
         belongs to the button that was pressed. */
      className={cx(
        'animate-scale-in absolute z-20 min-w-36 overflow-hidden rounded-xl',
        'border border-line bg-surface p-1 shadow-menu',
        side === 'down' ? 'top-full mt-1' : 'bottom-full mb-1',
        align === 'right'
          ? cx('right-0', side === 'down' ? 'origin-top-right' : 'origin-bottom-right')
          : cx('left-0', side === 'down' ? 'origin-top-left' : 'origin-bottom-left'),
        className,
      )}
    >
      {children}
    </div>
  )
}

export function MenuItem({
  onClick,
  icon,
  tone = 'default',
  children,
}: {
  onClick: () => void
  icon?: ReactNode
  tone?: 'default' | 'danger'
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={cx(
        'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors',
        tone === 'danger'
          ? 'text-danger hover:bg-danger/12'
          : 'text-fg-muted hover:bg-overlay/8 hover:text-fg',
      )}
    >
      {icon}
      {children}
    </button>
  )
}
