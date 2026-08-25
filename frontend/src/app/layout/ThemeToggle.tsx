// A three-way segmented control. The old cycling single icon could not
// communicate three states — with `system` added there is no icon that means
// "whatever the OS says" while also showing what that currently is.

import { Monitor, Moon, Sun } from 'lucide-react'

import { cx } from '@/lib/cx'
import { useTheme } from '@/lib/theme'
import type { ThemeMode } from '@/lib/theme'

const OPTIONS: { mode: ThemeMode; label: string; Icon: typeof Sun }[] = [
  { mode: 'light', label: 'Light', Icon: Sun },
  { mode: 'dark', label: 'Dark', Icon: Moon },
  { mode: 'system', label: 'System', Icon: Monitor },
]

export function ThemeToggle({ className }: { className?: string }) {
  const { mode, setMode } = useTheme()

  return (
    <fieldset className={cx('flex rounded-lg bg-overlay/6 p-0.5', className)}>
      <legend className="sr-only">Theme</legend>
      {OPTIONS?.map(({ mode: value, label, Icon }) => {
        const active = mode === value
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            title={label}
            onClick={() => setMode(value)}
            className={cx(
              'touch-target inline-flex size-7 items-center justify-center rounded-md transition-colors',
              active ? 'bg-surface text-fg shadow-lift' : 'text-fg-subtle hover:text-fg',
            )}
          >
            {/* Keyed on `active` so React remounts the icon and the rotation
                replays on selection, then settles. A persistent rotate-180
                would leave the crescent moon permanently upside-down. */}
            <Icon
              key={String(active)}
              className={cx('size-3.5', active && 'animate-swap')}
              aria-hidden="true"
            />
            <span className="sr-only">{label}</span>
          </button>
        )
      })}
    </fieldset>
  )
}
