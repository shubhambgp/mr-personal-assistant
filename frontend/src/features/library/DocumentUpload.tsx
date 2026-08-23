// Adding a document to the rep's own library.
//
// Deliberately NOT part of the composer's attach control, even though both take
// files. An image attached to a message is read once for that turn; a document
// is ingested — parsed, chunked, embedded — and then retrievable in every later
// conversation. Putting them behind the same paperclip would make a permanent,
// costly action look like a transient one.
//
// Uploads are private to the rep who made them: the server takes chair_id from
// the verified session, and there is no field for it in this request.

import { useRef, useState } from 'react'
import { FilePlus2, Loader2, Lock } from 'lucide-react'

import { Button } from '@/components/ui'
import { api, ApiError } from '@/lib/api'
import { fmtBytes } from '@/lib/format'

const ACCEPT = '.pdf,.docx'

export function DocumentUpload({ onUploaded }: { onUploaded?: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const submit = async (file: File) => {
    setBusy(`${file.name} · ${fmtBytes(file.size)}`)
    setResult(null)
    setError(null)
    try {
      const uploaded = await api.uploadDocument(file)
      setResult(
        `${uploaded.filename} added — ${uploaded.pages} page${uploaded.pages === 1 ? '' : 's'}, ` +
          `${uploaded.chunks} passage${uploaded.chunks === 1 ? '' : 's'} indexed`,
      )
      onUploaded?.()
    } catch (err) {
      // The server's message is specific and useful here — unsupported type, too
      // large, no extractable text — so it is shown rather than replaced.
      setError(err instanceof ApiError ? err.message : 'That upload did not work.')
    } finally {
      setBusy(null)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void submit(file)
        }}
      />

      <Button
        variant="outline"
        size="lg"
        className="w-full justify-start"
        disabled={busy !== null}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        ) : (
          <FilePlus2 className="size-4" aria-hidden="true" />
        )}
        {busy ? 'Reading…' : 'Add a document'}
      </Button>

      {busy && (
        <p className="px-1 text-2xs text-fg-subtle">
          {busy} — parsing and indexing. This happens once.
        </p>
      )}

      {result && (
        <p className="animate-rise rounded-lg bg-success/12 px-2 py-1.5 text-2xs text-success">
          {result}
        </p>
      )}

      {error && (
        <p
          role="alert"
          className="animate-rise rounded-lg bg-danger/12 px-2 py-1.5 text-2xs text-danger"
        >
          {error}
        </p>
      )}

      <p className="flex items-start gap-1.5 px-1 text-2xs text-fg-subtle">
        <Lock className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        <span>PDF or Word. Only you can see what you add.</span>
      </p>
    </div>
  )
}
