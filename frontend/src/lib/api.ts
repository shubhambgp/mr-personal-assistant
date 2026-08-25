// Typed API client. Every call is credentialed — auth is an httpOnly cookie, so
// the token is never in JS and cannot be read by injected script.

import type {
  Agenda,
  LibraryDocument,
  UploadedDocument,
  AgendaTask,
  TaskFilters,
  TaskList,
  TaskPatch,
  ApprovalCall,
  ComplianceReview,
  ConversationSummary,
  GoogleConnection,
  Rep,
} from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

/** Broadcast when any credentialed call comes back 401 mid-session, so the app
 *  can drop back to the login screen in one place. The 8-hour session cookie
 *  WILL expire during a working day; without this the rep is left in a
 *  signed-in-looking shell where every action fails differently. AuthProvider
 *  listens. See audit finding M-FE4. */
export const AUTH_EXPIRED_EVENT = 'auth:expired'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: 'include', ...init })
  if (!response.ok) {
    // A 401 on any non-auth call means the session lapsed mid-use. Auth
    // endpoints are excluded: /login returns 401 for a wrong password and /me
    // returns 401 on the normal signed-out boot — neither is a lapsed session.
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    }
    let detail = response.statusText
    try {
      // `.json()` is typed `any`; narrow it rather than trusting it — the
      // error body is the one response shape we never validate elsewhere.
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        const { detail: raw } = body
        if (typeof raw === 'string') detail = raw
      }
    } catch {
      /* non-JSON error body — keep the status text */
    }
    const retryAfter = Number(response.headers.get('Retry-After')) || undefined
    throw new ApiError(detail, response.status, retryAfter)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  login: (identifier: string, password: string) =>
    request<Rep>('/api/auth/login', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ identifier, password }),
    }),

  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),

  me: () => request<Rep>('/api/auth/me'),

  vintage: () => request<{ summary: string }>('/api/vintage'),

  conversations: () => request<ConversationSummary[]>('/api/conversations'),

  conversation: (id: string) =>
    request<{ conversation_id: string; messages: RawMessage[] }>(`/api/conversations/${id}`),

  renameConversation: (id: string, title: string) =>
    request<void>(`/api/conversations/${id}`, {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify({ title }),
    }),

  deleteConversation: (id: string) =>
    request<void>(`/api/conversations/${id}`, { method: 'DELETE' }),

  documents: () => request<LibraryDocument[]>('/api/documents'),

  /** Everything the Agenda panel renders, in one call. No model involved. */
  agenda: () => request<Agenda>('/api/agenda'),

  googleConnection: () => request<GoogleConnection>('/api/agenda/connection'),

  /** A full page load, not fetch: the server 302s to Google's consent screen,
   *  and an XHR cannot follow a cross-origin redirect into a login flow. */
  connectGoogleUrl: () => '/api/agenda/connect',

  disconnectGoogle: () => request<void>('/api/agenda/connection', { method: 'DELETE' }),

  /** The task browser. Filters go to the SERVER as query params rather than being
   *  applied here — status 'all' is unbounded, and filtering a truncated list in
   *  the browser reports "nothing" when it means "nothing in the first page".
   *
   *  `limit` exists for the one caller that wants only `counts` (the sidebar's
   *  overdue badge): the server computes counts over the whole filtered set, so
   *  limit=1 returns the same numbers without shipping up to 200 rows. */
  tasks: (filters: TaskFilters, limit = 200) => {
    const q = new URLSearchParams({ status: filters.status, limit: String(limit) })
    if (filters.important) q.set('important', 'true')
    if (filters.source) q.set('source', filters.source)
    if (filters.doctorId !== null) q.set('doctor_id', String(filters.doctorId))
    return request<TaskList>(`/api/agenda/tasks?${q.toString()}`)
  },

  addTask: (task: {
    title: string
    due_date?: string | null
    due_time?: string | null
    important?: boolean
    notes?: string | null
  }) =>
    request<AgendaTask>('/api/agenda/tasks', {
      method: 'POST',
      headers: JSON_HEADERS,
      // Only keys with a value are sent: the server's TaskCreate forbids extras
      // and treats an omitted field differently from an explicit null.
      body: JSON.stringify({
        title: task.title,
        ...(task.due_date ? { due_date: task.due_date } : {}),
        ...(task.due_time ? { due_time: task.due_time } : {}),
        ...(task.important ? { important: true } : {}),
        ...(task.notes ? { notes: task.notes } : {}),
      }),
    }),

  /** Patch a task. Send ONLY what changed — the server distinguishes an absent
   *  field from an explicit null, so `{important: true}` cannot blank a date. */
  patchTask: (id: string, patch: TaskPatch) =>
    request<AgendaTask>(`/api/agenda/tasks/${id}`, {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify(patch),
    }),

  setTaskDone: (id: string, done: boolean) =>
    request<AgendaTask>(`/api/agenda/tasks/${id}`, {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify({ done }),
    }),

  deleteTask: (id: string) => request<void>(`/api/agenda/tasks/${id}`, { method: 'DELETE' }),

  /** Adds one PDF or DOCX to this rep's own library. Not a chat attachment:
   *  images go to /api/chat/stream with the message, documents are ingested
   *  once and then retrievable in every later conversation. */
  uploadDocument: (file: File) => {
    const form = new FormData()
    form.set('file', file)
    return request<UploadedDocument>('/api/documents', { method: 'POST', body: form })
  },
}

/** A persisted message, as stored by app/services/conversations.py. */
export interface RawMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** Set when this turn is still waiting on the rep's approval. Without it a
   *  reload would lose the card AND leave the thread wedged behind an interrupt
   *  that is still pending in the graph checkpoint. */
  pending_approval?: {
    interrupt_id: string
    calls: ApprovalCall[]
    review: ComplianceReview | null
  } | null
  tool_calls: {
    call_id: string | null
    name: string | null
    input: Record<string, unknown> | null
    output: string | null
    is_error: boolean | null
    duration_ms: number | null
  }[]
  grounded: boolean | null
  unverified_claims: string[]
  created_at: string
}
