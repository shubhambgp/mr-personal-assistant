// Date helpers are timezone-sensitive, and both of these carry a fixed bug in
// their comments: bucketFor compares calendar days (not elapsed hours), and
// dueLabel parses bare YYYY-MM-DD as LOCAL midnight (new Date(iso) reads it as
// UTC and lands on the previous day west of Greenwich).

import { describe, expect, it } from 'vitest'

import { bucketFor, dueLabel } from './format'

// A fixed "now": Friday 15 May 2026, 10:00 local.
const NOW = new Date(2026, 4, 15, 10, 0)

describe('bucketFor', () => {
  it.each([
    ['same morning', new Date(2026, 4, 15, 0, 5), 'Today'],
    [
      'late last night — minutes old but a different day',
      new Date(2026, 4, 14, 23, 50),
      'Yesterday',
    ],
    ['six days back', new Date(2026, 4, 9, 12, 0), 'Previous 7 days'],
    ['eight days back', new Date(2026, 4, 7, 12, 0), 'Older'],
  ])('%s -> %s', (_label, when, expected) => {
    expect(bucketFor(when.toISOString(), NOW)).toBe(expected)
  })

  it('an unparseable stamp falls into Older rather than throwing', () => {
    expect(bucketFor('not-a-date', NOW)).toBe('Older')
  })
})

describe('dueLabel', () => {
  it('parses a bare date as LOCAL midnight — never the previous day', () => {
    // The regression: new Date('2026-05-15') is UTC midnight, which is 14 May
    // in any negative-offset zone. The helper must not do that.
    expect(dueLabel('2026-05-15', null, NOW)).toBe('Today')
  })

  it.each([
    ['2026-05-16', 'Tomorrow'],
    ['2026-05-14', 'Yesterday'],
  ])('%s -> %s', (iso, expected) => {
    expect(dueLabel(iso, null, NOW)).toBe(expected)
  })

  it('a null time means all-day: no 00:00 is ever shown', () => {
    expect(dueLabel('2026-05-15', null, NOW)).not.toMatch(/00:00|12:00/)
  })

  it('appends the time in the viewer clock format when one exists', () => {
    const label = dueLabel('2026-05-15', '15:30', NOW)
    expect(label.startsWith('Today ')).toBe(true)
    expect(label).toMatch(/3:30|15:30/) // 12h or 24h locale — both are correct
  })

  it('a garbled time degrades to the day alone', () => {
    expect(dueLabel('2026-05-15', 'xx:yy', NOW)).toBe('Today')
  })

  it('null date renders nothing', () => {
    expect(dueLabel(null, '10:00', NOW)).toBe('')
  })
})
