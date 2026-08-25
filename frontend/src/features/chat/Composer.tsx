// One rounded surface holding attachments, the input and the controls.
// Enter sends, Shift+Enter is a newline. While a turn is streaming the send
// button becomes Stop, which aborts the fetch — a rep who asked the wrong
// thing should not have to wait out the answer.

import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Loader2, Mic, Paperclip, Square } from 'lucide-react'

import { IconButton } from '@/components/ui'
import { api, ApiError } from '@/lib/api'
import { cx } from '@/lib/cx'
import { CONTENT_COL } from '@/lib/format'
import type { IngestedDocument } from '@/lib/types'

import { AttachmentTray } from './AttachmentTray'
import { useVoiceInput } from './useVoiceInput'
import { VoiceLevels } from './VoiceLevels'

/** A composer pick that is a document, not an image. Documents are routed to
 *  the Library (POST /api/documents — a permanent, per-rep ingest) before the
 *  message is sent, so a file shared in any conversation still ends up in the
 *  one place all files live. */
const DOC_PATTERN = /\.(pdf|docx)$/i

/** The single source of truth for the textarea's growth limit. There were
 *  three coupled constraints before — this constant, a `max-h-40` class and a
 *  JS-written inline height — which is three chances to disagree. */
const MAX_INPUT_HEIGHT_PX = 160

/** Server-side limits are the real enforcement (5 files / 15 MB); this is only
 *  so the rep sees the cap the moment they hit it. */
const MAX_FILES = 5

interface Props {
  /** `documents` are files just ingested into the Library — the turn needs to
   *  know they arrived, or the model answers "I don't see a PDF", and the rep
   *  needs to see which file their question was about. */
  onSend: (message: string, images: File[], documents?: IngestedDocument[]) => void
  onStop: () => void
  streaming: boolean
  disabled?: boolean
}

export function Composer({ onSend, onStop, streaming, disabled }: Props) {
  const [value, setValue] = useState('')
  const [images, setImages] = useState<File[]>([])
  const [documents, setDocuments] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadNote, setUploadNote] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // What was typed before dictation began. Recognition rebuilds its transcript
  // on every revision, so the composer value is always base + final + interim —
  // never appended, or every revised phrase would duplicate.
  const dictationBaseRef = useRef('')
  const voice = useVoiceInput((finalText, interim) => {
    setValue(dictationBaseRef.current + finalText + interim)
  })

  const toggleDictation = () => {
    if (voice.listening) {
      voice.stop()
      return
    }
    const current = value.replace(/\s+$/, '')
    dictationBaseRef.current = current ? `${current} ` : ''
    voice.start()
  }

  useEffect(() => {
    const node = textareaRef.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, MAX_INPUT_HEIGHT_PX)}px`
  }, [value])

  const canSend =
    !streaming &&
    !disabled &&
    !uploading &&
    (value.trim().length > 0 || images.length > 0 || documents.length > 0)

  const submit = async () => {
    if (!canSend) return
    if (voice.listening) voice.stop()
    setUploadError(null)
    setUploadNote(null)

    // Documents go to the Library FIRST, and a failure blocks the send: a rep
    // who attached a file and typed "summarise this" must not have the message
    // sail off while the file silently failed to ingest. Everything they typed
    // and attached is kept for the retry.
    const ingested: IngestedDocument[] = []
    if (documents.length > 0) {
      setUploading(true)
      try {
        for (const doc of documents) {
          // The server's own name for the file — that is what read_document and
          // list_documents will match on, so use it rather than doc.name.
          const uploaded = await api.uploadDocument(doc)
          ingested.push({ name: uploaded.filename, size: doc.size })
        }
      } catch (err) {
        setUploadError(err instanceof ApiError ? err.message : 'That document could not be added.')
        setUploading(false)
        return
      }
      setUploading(false)
      setDocuments([])
    }

    const message = value.trim()
    if (message || images.length > 0 || ingested.length > 0) {
      onSend(message, images, ingested)
    }
    setValue('')
    setImages([])
    if (fileRef.current) fileRef.current.value = ''
  }

  return (
    <div
      className={cx(
        'min-w-0 border-t border-line bg-page/85 px-3 pt-3 backdrop-blur-sm sm:px-6',
        // Safe-area: on a notched phone the composer otherwise sits under the
        // home indicator.
        'pb-[max(0.75rem,env(safe-area-inset-bottom))]',
      )}
    >
      <div className={CONTENT_COL}>
        <div
          className={cx(
            'rounded-input border border-line bg-surface px-2 py-2 shadow-lift transition-colors',
            'focus-within:border-accent/50',
          )}
        >
          {/* Inside the shell, not floating above it. */}
          <AttachmentTray
            files={images}
            onRemove={(index) => setImages((prev) => prev?.filter((_, i) => i !== index))}
            documents={documents}
            onRemoveDocument={(index) =>
              setDocuments((prev) => prev?.filter((_, i) => i !== index))
            }
          />

          {uploadError && (
            <p
              role="alert"
              className="animate-rise mb-1 rounded-lg bg-danger/12 px-2 py-1.5 text-2xs text-danger"
            >
              {uploadError}
            </p>
          )}
          {voice.error && (
            <p
              role="alert"
              className="animate-rise mb-1 rounded-lg bg-danger/12 px-2 py-1.5 text-2xs text-danger"
            >
              {voice.error}
            </p>
          )}
          {uploadNote && (
            <p
              role="status"
              className="animate-rise mb-1 rounded-lg bg-success/12 px-2 py-1.5 text-2xs text-success"
            >
              {uploadNote}
            </p>
          )}

          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            disabled={disabled}
            aria-label="Message"
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              // isComposing guard: without it Enter commits mid-composition
              // for Hindi, Japanese or any IME input, sending a half-typed
              // word. The old handler checked only shiftKey.
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void submit()
              }
            }}
            placeholder={
              streaming
                ? 'Answering… press Stop to interrupt'
                : voice.listening
                  ? 'Listening… speak, and your words appear here'
                  : 'Ask about your doctors, visits, or targets…'
            }
            className={cx(
              'w-full resize-none bg-transparent px-1.5 py-1 text-sm text-fg outline-none',
              'placeholder:text-fg-subtle disabled:opacity-60',
            )}
          />

          <div className="flex items-center justify-between gap-2 pt-1">
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif,.pdf,.docx"
              multiple
              className="hidden"
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? [])
                const docs = picked?.filter((f) => DOC_PATTERN.test(f.name))
                const imgs = picked?.filter((f) => !DOC_PATTERN.test(f.name))
                if (imgs.length) {
                  setImages((prev) => [...prev, ...imgs].slice(0, MAX_FILES))
                }
                if (docs.length) {
                  setDocuments((prev) => [...prev, ...docs].slice(0, MAX_FILES))
                }
                setUploadNote(null)
              }}
            />
            <div className="flex items-center gap-1">
              <IconButton
                label="Attach an image or document"
                title="Photos go with this message; PDF/Word files are added to your Library"
                onClick={() => fileRef.current?.click()}
                disabled={
                  streaming ||
                  disabled ||
                  uploading ||
                  (images.length >= MAX_FILES && documents.length >= MAX_FILES)
                }
              >
                <Paperclip className="size-4" aria-hidden="true" />
              </IconButton>

              {/* Dictation. Hidden entirely where the browser has no speech
                  recognition — a mic that cannot work is worse than no mic. */}
              {voice.supported && (
                <>
                  <IconButton
                    label={voice.listening ? 'Stop dictation' : 'Dictate your message'}
                    variant={voice.listening ? 'accent' : 'ghost'}
                    onClick={toggleDictation}
                    disabled={disabled}
                    aria-pressed={voice.listening}
                  >
                    <Mic className="size-4" aria-hidden="true" />
                  </IconButton>
                  {voice.listening && (
                    <>
                      <VoiceLevels analyserRef={voice.analyserRef} />
                      <span role="status" className="sr-only">
                        Listening — speak now.
                      </span>
                    </>
                  )}
                </>
              )}
            </div>

            {streaming ? (
              <IconButton label="Stop generating" variant="subtle" size="lg" onClick={onStop}>
                <Square className="size-3.5 fill-current" aria-hidden="true" />
              </IconButton>
            ) : (
              <IconButton
                label={uploading ? 'Adding to your Library…' : 'Send message'}
                variant="accent"
                size="lg"
                onClick={() => void submit()}
                disabled={!canSend}
              >
                {uploading ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <ArrowUp className="size-4" aria-hidden="true" />
                )}
              </IconButton>
            )}
          </div>
        </div>

        <p className="mt-2 text-center text-2xs text-fg-subtle">
          Answers come from your own book only. Numbers are checked against tool results.
        </p>
      </div>
    </div>
  )
}
