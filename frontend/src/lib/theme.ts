// Theme state. Three modes, one owner.
//
// The previous version was two states spread over three files, and read
// `prefers-color-scheme` only as a first-run fallback before locking to an
// explicit value forever — so a rep whose phone switched to dark at sunset saw
// no change. `system` is now a real, selectable, persisted mode with a live
// listener.

import { createContext, useContext } from 'react'

/** NOTE: this literal is duplicated in index.html's pre-paint script, which
 *  runs before any module exists and therefore cannot import it. That
 *  duplication is deliberate and accepted; both sites point at the other. */
export const THEME_KEY = 'qorvexa-theme'

export type ThemeMode = 'light' | 'dark' | 'system'

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system'
}

/** The mode a user chose, or `system` when nothing valid is stored. */
export function storedMode(): ThemeMode {
  try {
    const raw = localStorage.getItem(THEME_KEY)
    return isThemeMode(raw) ? raw : 'system'
  } catch {
    return 'system' // private mode / storage disabled
  }
}

export function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/** The three things a theme change has to touch. Two of them are outside
 *  Tailwind's reach entirely. */
export function applyTheme(resolved: 'light' | 'dark'): void {
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')

  // Native UI — scrollbars, the password-reveal glyph, date pickers, form
  // controls. No class can style these; `color-scheme` is what makes them
  // follow the theme instead of staying stubbornly light.
  root.style.colorScheme = resolved

  // Mobile browser chrome. In markup this can only vary by
  // prefers-color-scheme, which is wrong whenever the explicit choice
  // disagrees with the OS — so it is driven imperatively from here.
  // These literals mirror --page in styles/theme.css for each mode; change the
  // palette there and update these (and index.html's pre-paint script) together.
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (meta) meta.content = resolved === 'dark' ? '#141917' : '#f7f9f8'
}

export interface ThemeState {
  /** What the user chose: may be `system`. */
  mode: ThemeMode
  /** What is actually on screen right now. */
  resolved: 'light' | 'dark'
  setMode: (mode: ThemeMode) => void
}

export const ThemeContext = createContext<ThemeState | null>(null)

export function useTheme(): ThemeState {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme must be used inside <ThemeProvider>')
  return value
}
