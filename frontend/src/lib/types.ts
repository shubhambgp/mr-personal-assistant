// Mirrors the backend's SSE event contract in app/api/chat.py.
// Keep the two in step: a new event type there needs a case here, and the
// discriminated union means TypeScript will point at every place that needs it.

export type StreamEvent =
  | { type: 'start'; conversation_id: string }
  | { type: 'tool_start'; call_id: string; name: string; input: Record<string, unknown> }
  | {
      type: 'tool_end'
      call_id: string
      name: string
      input: Record<string, unknown>
      output: string
      is_error: boolean
      duration_ms: number
    }
  | { type: 'token'; delta: string }
  | { type: 'grounding'; grounded: boolean; unverified_claims: string[] }
  | { type: 'notice'; message: string }
  | {
      type: 'done'
      conversation_id: string
      response_id: string | null
      usage: { input_tokens: number; output_tokens: number; cached_tokens: number }
      timing: { total_ms: number; tool_ms: number; tool_share_pct: number | null }
    }
  | { type: 'error'; message: string }
  | {
      type: 'approval_required'
      conversation_id: string
      interrupt_id: string
      calls: ApprovalCall[]
      review: ComplianceReview | null
    }

/** One action waiting on the rep. `editable` names the args the SERVER will
 *  accept a change to — advisory here, because the card is not trusted: the
 *  graph filters every submitted edit against the tool's own whitelist. */
export interface ApprovalCall {
  id: string
  name: string
  args: Record<string, unknown>
  editable: string[]
}

export interface ComplianceFinding {
  rule: string
  severity: 'block' | 'warn'
  /** An exact span of the draft, so it can be shown against the text it came
   *  from. A finding the reviewer could not quote is dropped server-side. */
  quote: string
  basis: string
  guidance: string
}

export interface ComplianceReview {
  verdict: 'block' | 'warn' | 'clear'
  findings: ComplianceFinding[]
  requires_escalation: 'pharmacovigilance' | 'medical_information' | null
  reviewed_by: string
  note?: string
}

export interface PendingApproval {
  interruptId: string
  calls: ApprovalCall[]
  review: ComplianceReview | null
}

export interface ApprovalDecision {
  interruptId: string
  approved: boolean
  /** Per-call content edits, keyed by tool-call id. */
  edits: Record<string, Record<string, string>>
}

/** A tool call as the UI tracks it: created on tool_start, completed on tool_end. */
export interface ToolCall {
  callId: string
  name: string
  input: Record<string, unknown>
  output?: string
  isError?: boolean
  /* No duration/start-time fields: the duration UI was removed on purpose
     ("how long" is plumbing — /api/metrics records it for whoever needs the
     number), and stored-but-never-read state is a trap. The SSE `tool_end`
     event still CARRIES duration_ms — that is the backend contract — it is
     simply not kept. */
  status: 'running' | 'done' | 'error'
}

export interface Grounding {
  grounded: boolean
  unverifiedClaims: string[]
}

/** An image the rep attached. `previewUrl` is an object URL and exists only
 *  for the current session: images are not persisted server-side, so a resumed
 *  conversation has the name and nothing to render. See README limitations. */
export interface Attachment {
  name: string
  size: number
  previewUrl?: string
  /** Images travel WITH the message and are shown as a thumbnail; documents were
   *  ingested into the Library instead, so there is nothing to preview and the
   *  chip has to say where the file went. Absent means image, which is what
   *  every attachment was before documents could be attached. */
  kind?: 'image' | 'document'
}

/** A document the composer already ingested into the Library, named by the
 *  SERVER's filename — that is what `read_document` and `list_documents` match
 *  on, so the local `File.name` is not interchangeable with it. */
export interface IngestedDocument {
  name: string
  size: number
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolCalls: ToolCall[]
  grounding?: Grounding
  notices: string[]
  /** True while tokens are still arriving — drives the caret and the Stop button. */
  streaming?: boolean
  attachments?: Attachment[]
  /** Set when this turn paused for a human decision; cleared when it resumes.
   *
   *  An OBJECT that is always replaced wholesale, never a mutated array. That is
   *  deliberate: `patch` in useChatStream shallow-clones the draft and clones
   *  only `toolCalls` and `notices`, so a field that was pushed into would need
   *  to join that list — and the failure mode of forgetting is a card that never
   *  appears. Replacing the whole object means the shallow clone is enough. */
  pendingApproval?: PendingApproval
}

export interface Rep {
  chair_id: number
  rep_code: number
  rep_name: string
}

export interface ConversationSummary {
  id: string
  title: string | null
  created_at: string
  updated_at: string
  message_count: number
}

/** Shape every tool returns: {rows: [...]} or a single object. */
export interface ToolPayload {
  rows?: Record<string, unknown>[]
  row_count?: number
  error?: string
  [key: string]: unknown
}

/** One triaged mail thread, as the Agenda panel and list_mail both see it. */
export interface TriageItem {
  thread_id: string
  subject: string
  from_name: string
  from_address: string
  received_at: string | null
  days_waiting: number
  category: 'needs_reply' | 'follow_up_due' | 'awaiting_reply' | 'escalate' | 'fyi'
  reason: string
  doctor_id: number | null
  doctor_name: string | null
}

export interface CalendarEntry {
  event_id: string
  title: string
  start: string
  end: string
  location: string
  attendees: string[]
  all_day: boolean
  organiser_is_me: boolean
}

/** Which bucket a task falls in. Computed on the SERVER, never here: the panel
 *  and a chat answer must not be able to disagree, and "overdue" depends on a
 *  timezone the browser does not get a vote on. */
export type TaskSection = 'overdue' | 'today' | 'upcoming' | 'someday' | 'done'

export interface AgendaTask {
  id: string
  title: string
  notes: string | null
  due_date: string | null
  /** "HH:MM", or null for an all-day task. Null is not midnight — it means the
   *  rep never gave a time, which is a different thing. */
  due_time: string | null
  important: boolean
  /** Google's event id, when the task has been put on the calendar. */
  calendar_event_id: string | null
  doctor_id: number | null
  doctor_name: string | null
  source: 'rep' | 'assistant'
  done_at: string | null
  created_at: string
  section: TaskSection
}

export interface Agenda {
  as_of: string
  connected: boolean
  configured: boolean
  mail: TriageItem[]
  calendar: CalendarEntry[]
  tasks: AgendaTask[]
  counts: Partial<Record<string, number>>
  mail_error: string | null
  /** Distinguishes "never connected" from "connected and then expired", which
   *  `connected: false` alone cannot. */
  mail_state?: 'live' | 'stale' | 'absent'
}

/** The filter state of the task browser. Sent as query params, not applied here:
 *  status "all" is unbounded, so filtering a truncated list client-side would
 *  report "no done tasks" when it means "none in the first hundred". */
export interface TaskFilters {
  status: 'open' | 'done' | 'all'
  important: boolean
  source: 'rep' | 'assistant' | null
  doctorId: number | null
}

export interface TaskList {
  row_count: number
  counts: Record<TaskSection, number>
  rows: AgendaTask[]
  /** [id, name] pairs, taken from the tasks themselves — so the doctor filter
   *  can never become a directory listing. */
  doctors: [number, string][]
}

/** Fields a task edit may change. Absent means "leave alone"; an explicit null
 *  clears the value. */
export interface TaskPatch {
  title?: string
  due_date?: string | null
  due_time?: string | null
  important?: boolean
  notes?: string | null
  done?: boolean
}

export interface GoogleConnection {
  configured: boolean
  connected: boolean
  /** The grant died — expired, revoked, or the password changed. The credential
   *  has been deleted server-side but the address is kept, so Settings can say
   *  WHICH account to reconnect. */
  stale: boolean
  email_account: string | null
  scopes: string[]
  calendar_tz?: string | null
  why: string | null
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
