// Parsing `search_literature` results into citations. In its own module (not
// SourceList.tsx) because exporting a plain function from a component file
// trips react-refresh/only-export-components at --max-warnings 0 — and because
// a parser over tool output deserves unit tests, which import from here.

import type { ToolCall } from '@/lib/types'

export interface Source {
  document: string
  section: string
  page: number | null
  docType: string | null
}

/** Rows a `search_literature` call returned, deduplicated by document+section. */
export function extractSources(toolCalls: ToolCall[]): Source[] {
  const out = new Map<string, Source>()
  for (const call of toolCalls) {
    if (call.name !== 'search_literature' || !call.output) continue
    let parsed: unknown
    try {
      parsed = JSON.parse(call.output)
    } catch {
      continue // a malformed result is not worth a broken panel
    }
    if (!parsed || typeof parsed !== 'object') continue
    const rows = (parsed as { rows?: unknown }).rows
    if (!Array.isArray(rows)) continue
    for (const row of rows) {
      if (!row || typeof row !== 'object') continue
      const r = row as Record<string, unknown>
      const document = typeof r.document === 'string' ? r.document : null
      if (!document) continue
      const section = typeof r.section === 'string' ? r.section : ''
      const key = `${document}::${section}`
      if (!out.has(key)) {
        out.set(key, {
          document,
          section,
          page: typeof r.page === 'number' ? r.page : null,
          docType: typeof r.doc_type === 'string' ? r.doc_type : null,
        })
      }
    }
  }
  return [...out.values()]
}
