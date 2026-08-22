// Typed API client. Every call is credentialed — auth is an httpOnly cookie, so
// the token is never in JS and cannot be read by injected script.

import type {
  Agenda,
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

  /** Adds one PDF or DOCX to this rep's own library. Not a chat attachment:
   *  images go to /api/chat/stream with the message, documents are ingested
   *  once and then retrievable in every later conversation. */
  uploadDocument: (file: File) => {
    const form = new FormData()
    form.set('file', file)
    return request<UploadedDocument>('/api/documents', { method: 'POST', body: form })
  },
}

export interface LibraryDocument {
  document_id: string | null
  title: string | null
  /** The uploaded file's name — often the most recognisable label for a rep's
   *  own uploads. Null for documents ingested before the field was projected. */
  source_filename: string | null
  doc_type: string | null
  brand: string | null
  molecule: string | null
  version: string | null
  effective_date: string | null
  scope: string | null
  pages: number | null
  /** Null for anything ingested before the timestamp existed — render nothing,
   *  never "Invalid Date". */
  ingested_at: string | null
}

export interface UploadedDocument {
  filename: string
  status: string
  pages: number
  chunks: number
  detail: string
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
