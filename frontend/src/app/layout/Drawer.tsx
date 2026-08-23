// The mobile sidebar. The version this replaces had no focus trap, no Escape,
// no body-scroll lock, and its scrim vanished instantly instead of fading —
// `display` is not transitionable, so the fade never ran.

import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

import { cx } from '@/lib/cx'

import { useIsMobile } from './useIsMobile'

export function Drawer({
  open,
  onClose,
  collapsed = false,
  children,
}: {
  open: boolean
  onClose: () => void
  /** Desktop icon rail: narrows the lg sidebar. Mobile is untouched — every
   *  collapse class below is lg:-prefixed, and the drawer only opens below lg. */
  collapsed?: boolean
  children: ReactNode
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  // Below lg the panel is a real drawer; at lg it is permanent layout. That
  // distinction drives both the `inert` and the `role` below, so a closed mobile
  // drawer is not keyboard-/SR-reachable and the desktop sidebar is not
  // announced as a dialog. See audit finding M-FE1 / M-FE9.
  const isMobile = useIsMobile()

  useEffect(() => {
    if (!open) return

    returnFocusRef.current = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    panel?.querySelector<HTMLElement>('button, [href], input, [tabindex]:not([tabindex="-1"])')?.focus()

    // Locking the body is what stops the page behind the drawer scrolling
    // under your thumb on a phone.
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      if (e.key !== 'Tab' || !panel) return
      // Focus trap: without it Tab walks straight out of the drawer into the
      // chat behind it, which is inert to the eye but not to the keyboard.
      const focusable = [
        ...panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ].filter((el) => el.offsetParent !== null)
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!first || !last) return
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
      returnFocusRef.current?.focus()
    }
  }, [open, onClose])

  return (
    <>
      {/* Deliberately NOT a token: an overlay that inverted with the theme
          would be a white scrim in dark mode. */}
      <div
        aria-hidden="true"
        onClick={onClose}
        className={cx(
          'fixed inset-0 z-30 bg-black/40 transition-opacity lg:hidden',
          // allow-discrete keeps the element around long enough for the
          // opacity transition to actually run on the way out.
          '[transition-behavior:allow-discrete]',
          open ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
      />

      <div
        ref={panelRef}
        // Only a dialog while it behaves like one (mobile). On desktop it is
        // static layout; the <nav> inside it provides the landmark.
        role={isMobile ? 'dialog' : undefined}
        aria-modal={isMobile ? open : undefined}
        aria-label={isMobile ? 'Conversations' : undefined}
        // Closed drawer on mobile: removed from the tab order and the a11y tree,
        // so a keyboard/SR user does not walk through an off-screen sidebar.
        {...(isMobile && !open ? { inert: true } : {})}
        className={cx(
          'fixed inset-y-0 left-0 z-40 flex w-[85vw] max-w-xs flex-col border-r border-line bg-sunken',
          'transition-transform duration-(--duration-mid)',
          // Safe-area on the left for a notched phone held in landscape.
          'pl-[env(safe-area-inset-left)]',
          open ? 'translate-x-0' : '-translate-x-full',
          // At lg: it stops being a drawer and becomes part of the layout.
          // Width is the ONLY thing collapse changes, and it is deliberately
          // not animated — lg:transition-none exists because a width transition
          // reflows the chat grid mid-animation.
          //
          'lg:static lg:z-auto lg:max-w-none lg:translate-x-0 lg:transition-none',
          collapsed ? 'lg:w-14' : 'lg:w-72',
        )}
      >
        {children}
      </div>
    </>
  )
}
