// The only writer of the `.dark` class.

import { useEffect, useLayoutEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import {
  applyTheme,
  storedMode,
  systemPrefersDark,
  THEME_KEY,
  ThemeContext,
} from '@/lib/theme'
import type { ThemeMode } from '@/lib/theme'

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(storedMode)

  // The only genuinely external input is the OS preference. Everything else is
  // derived from it and `mode`, so there is no second copy of the answer to
  // fall out of step.
  const [systemDark, setSystemDark] = useState(systemPrefersDark)

  const resolved: 'light' | 'dark' =
    mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  // useLayoutEffect, not useEffect: this runs before paint. The pre-paint
  // script in index.html has already put the right class on <html> using the
  // same key and the same fallback, so the two agree by construction — but if
  // they ever drifted, a post-paint correction would be a visible flash of the
  // wrong theme, which is precisely what that script exists to prevent.
  useLayoutEffect(() => {
    applyTheme(resolved)
  }, [resolved])

  useEffect(() => {
    try {
      localStorage.setItem(THEME_KEY, mode)
    } catch {
      /* storage disabled — the choice simply does not survive a reload */
    }
  }, [mode])

  // Subscribed only while following the OS, and detached otherwise: in an
  // explicit `light` or `dark`, an OS change must do nothing at all. The
  // previous implementation read prefers-color-scheme once as a first-run
  // fallback and then locked to a fixed value forever, so a rep whose phone
  // switched to dark at sunset saw no change.
  useEffect(() => {
    if (mode !== 'system') return
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [mode])

  const value = useMemo(() => ({ mode, resolved, setMode }), [mode, resolved])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
