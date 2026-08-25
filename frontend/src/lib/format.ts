// Formatting and grouping helpers shared across features.

/** The message column width. Exported as a constant rather than left as a
 *  duplicated class because MessageList and Composer must agree exactly — if
 *  they drift the composer visibly desyncs from the messages above it, and a
 *  TypeScript constant is grep-able in a way a repeated class string is not. */
export const CONTENT_COL = 'mx-auto w-full max-w-3xl'

export function fmtCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return JSON.stringify(value) ?? ''
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Buckets used to group the conversation list. `updated_at` was already
 *  fetched for every conversation and rendered nowhere. */
export type DateBucket = 'Today' | 'Yesterday' | 'Previous 7 days' | 'Older'

export const DATE_BUCKETS: readonly DateBucket[] = [
  'Today',
  'Yesterday',
  'Previous 7 days',
  'Older',
]

export function bucketFor(iso: string, now = new Date()): DateBucket {
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return 'Older'
  // Compare calendar days, not elapsed hours: something from 23:50 last night
  // belongs in Yesterday even though it is only minutes old.
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const days = Math.floor(
    (startOfToday.getTime() -
      new Date(then.getFullYear(), then.getMonth(), then.getDate()).getTime()) /
      86_400_000,
  )
  if (days <= 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days <= 7) return 'Previous 7 days'
  return 'Older'
}

/** A task's due date and optional time, as a rep would say it.
 *
 * "Today", "Tomorrow", "Yesterday", else "Fri 28 Aug". A time is appended when
 * there is one — `null` means all day, which is NOT midnight, so it must not
 * render as 00:00.
 *
 * Deliberately not bucketFor(): that buckets backwards into Yesterday/Older for
 * a conversation list, and a due date runs forwards.
 */
export function dueLabel(isoDate: string | null, time: string | null, now = new Date()): string {
  if (!isoDate) return ''
  // Parsed as local midnight rather than through `new Date(iso)`, which reads a
  // bare YYYY-MM-DD as UTC and lands on the previous day west of Greenwich.
  const parts = isoDate.split('-').map(Number)
  const [y, m, d] = parts
  if (!y || !m || !d) return isoDate
  const when = new Date(y, m - 1, d)

  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const days = Math.round((when.getTime() - midnight.getTime()) / 86_400_000)

  let day: string
  if (days === 0) day = 'Today'
  else if (days === 1) day = 'Tomorrow'
  else if (days === -1) day = 'Yesterday'
  else if (days > 1 && days < 7) day = when.toLocaleDateString(undefined, { weekday: 'long' })
  else
    day = when.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })

  if (!time) return day
  // "15:30" -> the viewer's own clock format, so a 12-hour locale sees 3:30 pm.
  const [hh, mm] = time.split(':').map(Number)
  if (hh === undefined || mm === undefined || Number.isNaN(hh) || Number.isNaN(mm)) return day
  const stamp = new Date(y, m - 1, d, hh, mm)
  return `${day} ${stamp.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`
}
