// Per-tool presentation. Deliberately holds *no class strings*: Tailwind v4
// has no `content` array and detects classes by scanning source text, so a
// class assembled from a lookup at runtime is never emitted. Icons and labels
// are safe here; a `Record<string, string>` of colour classes would not be.

import {
  BookOpen,
  CalendarClock,
  CalendarDays,
  CalendarPlus,
  CalendarX,
  CheckCheck,
  Database,
  FileText,
  Gauge,
  Inbox,
  Library,
  ListChecks,
  ListTodo,
  Mail,
  Pill,
  PlugZap,
  Search,
  Send,
  Store,
  Wrench,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface ToolMeta {
  label: string
  Icon: LucideIcon
}

const TOOLS: Record<string, ToolMeta> = {
  find_doctor: { label: 'Looking up doctor', Icon: Search },
  get_doctor_brief: { label: 'Building pre-call briefing', Icon: FileText },
  get_doctor_hooks: { label: 'Fetching talking points', Icon: ListChecks },
  get_doctor_brands: { label: 'Fetching brand performance', Icon: Pill },
  list_pending_visits: { label: 'Checking pending visits', Icon: CalendarClock },
  get_visit_summary: { label: 'Summarising visits', Icon: CalendarClock },
  get_rep_scorecard: { label: 'Reading your scorecard', Icon: Gauge },
  get_doctor_chemists: { label: 'Finding tagged chemists', Icon: Store },
  get_daily_plan: { label: 'Building today’s plan', Icon: ListChecks },
  run_sql: { label: 'Running a custom query', Icon: Database },
  search_literature: { label: 'Searching product literature', Icon: BookOpen },
  list_documents: { label: 'Checking available documents', Icon: Library },
  open_agenda: { label: 'Opening your agenda', Icon: Inbox },
  agenda_status: { label: 'Checking your connection', Icon: PlugZap },
  list_mail: { label: 'Triaging your inbox', Icon: Inbox },
  get_mail: { label: 'Reading a mail thread', Icon: Mail },
  send_email: { label: 'Preparing an email', Icon: Send },
  list_calendar: { label: 'Checking your calendar', Icon: CalendarDays },
  create_event: { label: 'Preparing a meeting', Icon: CalendarPlus },
  search_mail: { label: 'Searching your mail', Icon: Search },
  update_event: { label: 'Preparing a change', Icon: CalendarClock },
  cancel_event: { label: 'Preparing a cancellation', Icon: CalendarX },
  list_tasks: { label: 'Reading your tasks', Icon: ListTodo },
  create_task: { label: 'Adding a task', Icon: ListTodo },
  update_task: { label: 'Updating a task', Icon: ListTodo },
  complete_task: { label: 'Ticking off a task', Icon: CheckCheck },
  schedule_task: { label: 'Preparing to block time', Icon: CalendarPlus },
}

/** `noUncheckedIndexedAccess` makes the lookup `ToolMeta | undefined`, so the
 *  fallback is enforced by the compiler rather than remembered. */
export function toolMeta(name: string): ToolMeta {
  return TOOLS[name] ?? { label: name, Icon: Wrench }
}
