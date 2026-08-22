// Every URL in the app, defined once. Components never hard-code a path —
// they import from here, so a route can be renamed in one place and the
// compiler finds every navigation that needs updating.

export const ROUTES = {
  login: '/login',
  /** The chat — the app's home. */
  assistant: '/personal-assistant',
  /** One conversation, deep-linkable and reload-safe. */
  conversation: (id: string) => `/personal-assistant/${id}`,
  library: '/library',
  agenda: '/agenda',
  settings: '/settings',
} as const

/** Route PATTERNS for <Route path=...> declarations — kept beside the
 *  builders above so the two can never drift. */
export const ROUTE_PATTERNS = {
  login: '/login',
  assistant: '/personal-assistant',
  conversation: '/personal-assistant/:conversationId',
  library: '/library',
  agenda: '/agenda',
  settings: '/settings',
} as const

/** The sidebar's view name for a pathname — the app's routing is these four
 *  panes swapped in the middle grid row. */
export function viewForPath(pathname: string): 'chat' | 'agenda' | 'settings' | 'library' {
  if (pathname.startsWith(ROUTES.agenda)) return 'agenda'
  if (pathname.startsWith(ROUTES.settings)) return 'settings'
  if (pathname.startsWith(ROUTES.library)) return 'library'
  return 'chat'
}

export function pathForView(view: 'chat' | 'agenda' | 'settings' | 'library'): string {
  switch (view) {
    case 'agenda':
      return ROUTES.agenda
    case 'settings':
      return ROUTES.settings
    case 'library':
      return ROUTES.library
    default:
      return ROUTES.assistant
  }
}
