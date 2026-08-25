import type { ReactNode } from 'react'

import { cx } from '@/lib/cx'

type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'accent'

/* The /12 wash replaces five `bg-*-100 dark:bg-*-950` pairs. A semantic colour
   at 12% over any surface reads correctly in both themes, so there is not one
   dark: variant here. */
const TONES: Record<Tone, string> = {
  neutral: 'bg-overlay/8 text-fg-subtle',
  success: 'bg-success/12 text-success',
  warning: 'bg-warning/12 text-warning',
  danger: 'bg-danger/12 text-danger',
  accent: 'bg-accent/12 text-accent-ink',
}

export function Badge({
  tone = 'neutral',
  className,
  children,
}: {
  tone?: Tone
  className?: string
  children: ReactNode
}) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-2xs font-medium',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
