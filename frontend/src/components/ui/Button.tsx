import type { ButtonHTMLAttributes } from 'react'

import { cx } from '@/lib/cx'

type Variant = 'primary' | 'ghost' | 'subtle' | 'danger' | 'outline'
type Size = 'sm' | 'md' | 'lg'

/* Hover always moves *toward* accent-ink — darker in light mode, lighter in
   dark — so `bg-accent hover:bg-accent-ink` is correct in both themes and no
   --accent-hover token is needed. `overlay` is ink with an opacity modifier,
   which is why ghost and subtle carry no dark: variant at all. */
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-accent-fg hover:bg-accent-ink active:scale-[.98]',
  ghost:   'text-fg-muted hover:bg-overlay/6 hover:text-fg',
  subtle:  'bg-overlay/6 text-fg hover:bg-overlay/10',
  outline: 'border border-line bg-surface text-fg hover:border-line-strong hover:bg-overlay/4',
  danger:  'bg-danger/12 text-danger hover:bg-danger/20',
}

const SIZES: Record<Size, string> = {
  sm: 'h-7 gap-1 rounded-lg px-2 text-2xs',
  md: 'h-9 gap-1.5 rounded-lg px-3 text-xs',
  lg: 'h-10 gap-2 rounded-xl px-4 text-sm',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

export function Button({ variant = 'primary', size = 'md', className, ...rest }: ButtonProps) {
  return (
    <button
      className={cx(
        'inline-flex shrink-0 items-center justify-center font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100',
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      {...rest}
    />
  )
}
