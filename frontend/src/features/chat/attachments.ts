// Which pile a picked file belongs in — and the third pile the composer did not
// used to have.
//
// The old split was `!DOC_PATTERN.test(name)`: *anything that is not a .pdf or
// .docx is an image*. So a `.heic` off a phone — or a .txt, or a .zip — became an
// `<img>` pointed at an object URL the browser could never decode, and the rep
// got the broken-image glyph in the tray with the filename spilling out of it.
// They then sent it anyway and the server answered "not a supported image".
//
// `accept` on the file input does NOT prevent this. It is a filter hint: the
// picker still offers "All files", and a paste or a drop ignores it entirely.
//
// Mirrors backend/app/bot/attachments.py, which stays the enforcement. This
// exists so the rep is told at pick time rather than after sending.
//
// Its own module, not a helper inside Composer.tsx, because a component file
// that also exports a function trips react-refresh — the same reason
// `sources.ts` sits beside `SourceList.tsx`.

/** Exactly the MIME types backend/app/bot/attachments.py accepts. */
export const IMAGE_MIMES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'] as const

const IMAGE_EXTENSIONS = /\.(png|jpe?g|webp|gif)$/i
const DOC_EXTENSIONS = /\.(pdf|docx)$/i

/** What the file input should offer. Derived, so the hint and the check agree. */
export const ACCEPT_ATTRIBUTE = [...IMAGE_MIMES, '.pdf', '.docx'].join(',')

export interface SortedPicks {
  /** Sent with the message as multimodal parts. */
  images: File[]
  /** Ingested into the rep's Library first — a permanent, per-rep action. */
  documents: File[]
  /** Neither, so the rep hears about it now instead of after sending. */
  rejected: File[]
}

/** Declared type first, extension second — the order
 *  `attachments.py:_resolve_mime` uses, because browsers are inconsistent about
 *  `type` on some platforms and an empty string is common on Android. */
function isImage(file: File): boolean {
  if ((IMAGE_MIMES as readonly string[]).includes(file.type)) return true
  return IMAGE_EXTENSIONS.test(file.name)
}

function isDocument(file: File): boolean {
  if (file.type === 'application/pdf') return true
  return DOC_EXTENSIONS.test(file.name)
}

/** Sort a pick into the three piles. Order within each pile is preserved. */
export function sortPicks(picked: File[]): SortedPicks {
  const images: File[] = []
  const documents: File[] = []
  const rejected: File[] = []
  // Documents are tested FIRST: a PDF's declared type is unambiguous, and
  // testing images first would only matter for a file claiming to be both.
  for (const file of picked) {
    if (isDocument(file)) documents.push(file)
    else if (isImage(file)) images.push(file)
    else rejected.push(file)
  }
  return { images, documents, rejected }
}

/** What to tell the rep about files that went nowhere. Names them, because
 *  "some files were skipped" leaves them counting thumbnails to work out which. */
export function unsupportedMessage(rejected: File[]): string | null {
  if (!rejected.length) return null
  const names = rejected?.map((f) => f.name).join(', ')
  return `Could not attach ${names} — photos must be PNG, JPEG, WEBP or GIF, and documents PDF or Word. A phone photo saved as HEIC needs converting first.`
}

/** A picked image and the object URL that previews it.
 *
 *  The pair is created together and released together, because the URL's
 *  lifetime is the FILE's lifetime — not a component's mount. A thumbnail
 *  component that created its own URL and revoked it in an effect cleanup was
 *  broken by StrictMode's mount → cleanup → mount: the cleanup revoked the URL
 *  and nothing re-created it, so every preview in development was a dead blob.
 *  Owning it here, where the files are owned, removes the question.
 */
export interface PickedImage {
  file: File
  url: string
}

export function toPickedImages(files: File[]): PickedImage[] {
  return files?.map((file) => ({ file, url: URL.createObjectURL(file) }))
}

/** Release the previews. Call it on remove, after send, and on unmount — an
 *  object URL pins the whole file in memory until it is revoked, and these are
 *  multi-megabyte phone photos. */
export function releasePreviews(images: PickedImage[]): void {
  for (const image of images) URL.revokeObjectURL(image.url)
}
