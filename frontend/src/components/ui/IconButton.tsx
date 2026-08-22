import type { ButtonHTMLAttributes } from 'react'

import { cx } from '@/lib/cx'

type Variant = 'ghost' | 'subtle' | 'danger' | 'accent'

const VARIANTS: Record<Variant, string> = {
  ghost:  'text-fg-subtle hover:bg-overlay/8 hover:text-fg',
  subtle: 'bg-overlay/6 text-fg-muted hover:bg-overlay/12 hover:text-fg',
  danger: 'text-fg-subtle hover:bg-danger/12 hover:text-danger',
  accent: 'bg-accent text-accent-fg hover:bg-accent-ink active:scale-90',
}

const SIZES = {
  sm: 'size-7 rounded-md',
  md: 'size-8 rounded-lg',
  lg: 'size-9 rounded-xl',
} as const

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: the icon itself is aria-hidden, so without this the control is
   *  invisible to a screen reader. */
  label: string
  variant?: Variant
  size?: keyof typeof SIZES
}

/** An icon-only control. `touch-target` keeps the glyph small while expanding
 *  the hit area to 44px on coarse pointers — the visible button stays 28–36px
 *  on desktop, which is what the design wants, without being a 28px tap
 *  target on a phone. */
export function IconButton({
  label,
  variant = 'ghost',
  size = 'md',
  className,
  ...rest
}: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cx(
        'touch-target inline-flex shrink-0 items-center justify-center transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-40',
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      {...rest}
    />
  )
}
