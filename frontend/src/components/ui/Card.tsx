import type { HTMLAttributes } from 'react'

import { cx } from '@/lib/cx'

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cx('rounded-card border border-line bg-surface shadow-lift', className)}
      {...rest}
    />
  )
}
