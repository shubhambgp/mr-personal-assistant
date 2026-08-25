// The Library: everything the assistant can retrieve from when it answers.
//
// Two sections, split on scope. "My uploads" (scope=chair) comes first because
// it is the part the rep controls; "Company literature" (scope=global) is the
// shared corpus every rep sees. The filter is client-side on purpose: the
// backend caps the list at 100 documents, so a server-side q parameter would be
// a new API surface for no gain.

import { useMemo, useState } from 'react'
import { BookOpen, Lock, Search, X } from 'lucide-react'

import { Badge, Card, IconButton, Skeleton } from '@/components/ui'
import type { LibraryDocument } from '@/lib/types'
import { CONTENT_COL } from '@/lib/format'
import { cx } from '@/lib/cx'

import { DocumentUpload } from './DocumentUpload'
import { useLibrary } from './useLibrary'

export function LibraryPanel({ active }: { active: boolean }) {
  const { documents, loading, error, refresh } = useLibrary(active)
  const [query, setQuery] = useState('')

  const { mine, shared } = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matches = (d: LibraryDocument) =>
      !q ||
      [d.title, d.source_filename, d.brand, d.molecule]?.some((field) =>
        field?.toLowerCase().includes(q),
      )
    const visible = documents?.filter(matches)
    return {
      mine: visible?.filter((d) => d.scope === 'chair'),
      shared: visible?.filter((d) => d.scope !== 'chair'),
    }
  }, [documents, query])

  return (
    <div className="scrollbar-thin min-h-0 min-w-0 overflow-y-auto px-4 py-5 sm:px-6">
      <div className={cx(CONTENT_COL, 'flex flex-col gap-5')}>
        <header>
          <h2 className="font-serif text-xl text-fg">Library</h2>
          <p className="text-2xs text-fg-subtle">
            What the assistant can retrieve from when it answers you.
          </p>
        </header>

        {error && (
          <p role="alert" className="rounded-card bg-danger/12 px-3 py-2 text-2xs text-danger">
            {error}
          </p>
        )}

        <Card className="p-4">
          <DocumentUpload onUploaded={() => void refresh()} />
        </Card>

        <div className="relative">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-fg-subtle"
            aria-hidden="true"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by name, brand or molecule…"
            aria-label="Filter documents"
            className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-8 text-xs text-fg outline-none transition-colors focus:border-accent"
          />
          {query && (
            <IconButton
              label="Clear filter"
              size="sm"
              onClick={() => setQuery('')}
              className="absolute right-1 top-1/2 -translate-y-1/2"
            >
              <X className="size-3.5" aria-hidden="true" />
            </IconButton>
          )}
        </div>

        {loading ? (
          <div className="space-y-2">
            {[0, 1, 2]?.map((i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
            <p role="status" className="sr-only">
              Loading your library…
            </p>
          </div>
        ) : (
          <>
            <Section
              title="My uploads"
              documents={mine}
              empty={
                query ? 'No uploads match.' : 'Nothing yet. Documents you add are private to you.'
              }
            />
            <p className="flex items-start gap-1.5 px-1 text-2xs text-fg-subtle">
              <Lock className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
              <span>
                Documents are parsed and indexed for retrieval; the original file is not kept.
              </span>
            </p>

            <Section
              title="Company literature"
              documents={shared}
              empty={query ? 'No company documents match.' : 'No shared literature loaded.'}
            />
          </>
        )}
      </div>
    </div>
  )
}

function Section({
  title,
  documents,
  empty,
}: {
  title: string
  documents: LibraryDocument[]
  empty: string
}) {
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-2 text-label uppercase text-fg-subtle">
        {title}
        <Badge tone="neutral">{documents.length}</Badge>
      </h2>
      {documents.length === 0 ? (
        <p className="rounded-card bg-overlay/6 px-3 py-4 text-center text-2xs text-fg-subtle">
          {empty}
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {documents?.map((doc, i) => (
            <DocumentRow key={doc.document_id ?? `${title}-${i}`} doc={doc} />
          ))}
        </ul>
      )}
    </section>
  )
}

function DocumentRow({ doc }: { doc: LibraryDocument }) {
  const name = doc.title || doc.source_filename || 'Untitled document'
  const meta = [
    doc.brand,
    doc.molecule,
    doc.pages != null ? `${doc.pages} page${doc.pages === 1 ? '' : 's'}` : null,
  ]?.filter(Boolean)
  // Null for anything ingested before the timestamp existed — render nothing
  // rather than "Invalid Date".
  const added = doc.ingested_at ? new Date(doc.ingested_at) : null

  return (
    /* min-w-0 on the text column is load-bearing: a long filename must truncate
       rather than push the page into horizontal scroll. */
    <li className="flex items-start gap-3 rounded-card border border-line bg-surface px-3 py-2.5">
      <BookOpen className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="min-w-0 truncate text-xs font-medium text-fg">{name}</span>
          {doc.doc_type && <Badge tone="neutral">{doc.doc_type}</Badge>}
        </div>
        {(meta.length > 0 || added) && (
          <p className="mt-0.5 truncate text-2xs text-fg-subtle">
            {meta.join(' · ')}
            {added && !Number.isNaN(added.getTime()) && (
              <>
                {meta.length > 0 && ' · '}Added {added.toLocaleDateString()}
              </>
            )}
          </p>
        )}
      </div>
    </li>
  )
}
