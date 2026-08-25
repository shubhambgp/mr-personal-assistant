// Thumbnails, not filename chips. The old tray showed a name and never an
// image — there was no <img> and no createObjectURL anywhere in the app, so a
// rep photographing an RCPA sheet had no confirmation they had attached the
// right one.

import { useEffect, useState } from 'react'
import { FileText, X } from 'lucide-react'

import { cx } from '@/lib/cx'
import { fmtBytes } from '@/lib/format'

export function AttachmentTray({
  files,
  onRemove,
  documents = [],
  onRemoveDocument,
}: {
  files: File[]
  onRemove: (index: number) => void
  /** PDFs/DOCX picked in the composer. Rendered as labelled chips, not thumbs:
   *  a document is INGESTED into the rep's library — a permanent action — and
   *  the chip says so, so it never looks like a transient image attachment. */
  documents?: File[]
  onRemoveDocument?: (index: number) => void
}) {
  if (!files.length && !documents.length) return null
  return (
    <ul className="mb-2 flex flex-wrap items-center gap-2 px-1">
      {files?.map((file, i) => (
        <Thumb key={`${file.name}-${file.size}-${i}`} file={file} onRemove={() => onRemove(i)} />
      ))}
      {documents?.map((file, i) => (
        <DocChip
          key={`${file.name}-${file.size}-${i}`}
          file={file}
          onRemove={onRemoveDocument ? () => onRemoveDocument(i) : undefined}
        />
      ))}
    </ul>
  )
}

function DocChip({ file, onRemove }: { file: File; onRemove?: () => void }) {
  return (
    <li
      className="animate-pop flex items-center gap-1.5 rounded-xl border border-line bg-sunken px-2 py-1.5"
      title={`${file.name} · ${fmtBytes(file.size)}`}
    >
      <FileText className="size-3.5 shrink-0 text-fg-subtle" aria-hidden="true" />
      <span className="max-w-36 min-w-0 truncate text-2xs text-fg">{file.name}</span>
      <span className="shrink-0 rounded-sm bg-accent-soft px-1 py-0.5 text-2xs text-accent-ink">
        adds to your Library
      </span>
      {onRemove && (
        <button
          type="button"
          aria-label={`Remove ${file.name}`}
          onClick={onRemove}
          className="shrink-0 rounded-full p-0.5 text-fg-subtle transition-colors hover:text-danger"
        >
          <X className="size-3" aria-hidden="true" />
        </button>
      )}
    </li>
  )
}

function Thumb({ file, onRemove }: { file: File; onRemove: () => void }) {
  // Created during the first render rather than in an effect: an effect would
  // paint one frame with no image, and would set state as a cascading render.
  const [url] = useState(() => URL.createObjectURL(file))
  const [removing, setRemoving] = useState(false)

  // Revoked on unmount. An object URL pins the entire file in memory until
  // released, and these are multi-megabyte phone photos.
  useEffect(() => () => URL.revokeObjectURL(url), [url])

  return (
    <li
      className={cx(
        'group/thumb relative',
        // Removal is animated out over 160ms rather than vanishing, so the
        // tray does not appear to jump for no reason.
        removing ? 'animate-[pop_160ms_reverse_both]' : 'animate-pop',
      )}
    >
      <img
        src={url}
        alt={file.name}
        title={`${file.name} · ${fmtBytes(file.size)}`}
        className="size-14 rounded-xl border border-line object-cover"
      />
      <button
        type="button"
        aria-label={`Remove ${file.name}`}
        onClick={() => {
          setRemoving(true)
          setTimeout(onRemove, 160)
        }}
        className={cx(
          'absolute -right-1.5 -top-1.5 flex size-5 items-center justify-center rounded-full',
          'border border-line bg-surface text-fg-subtle shadow-lift transition-colors',
          'hover:text-danger',
          // Always visible on touch, where there is no hover to reveal it.
          'opacity-100 md:opacity-0 md:group-hover/thumb:opacity-100 md:group-focus-within/thumb:opacity-100',
        )}
      >
        <X className="size-3" aria-hidden="true" />
      </button>
    </li>
  )
}
