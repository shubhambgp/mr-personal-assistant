import { cx } from '@/lib/cx'

/** A shimmer sweep rather than a pulse: `animate-pulse` fades the whole block
 *  in and out, which reads as *disabled*. A sweep reads as loading. The
 *  gradient itself lives in styles/base.css as `shimmer-sweep` — an inline
 *  arbitrary gradient cannot use the `/8` opacity modifier, and would be
 *  unreadable in a className even if it could. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cx('shimmer-sweep animate-shimmer', className)} />
}
