import { describe, expect, it } from 'vitest'

import { ACCEPT_ATTRIBUTE, IMAGE_MIMES, sortPicks, unsupportedMessage } from './attachments'

const file = (name: string, type = '') => new File(['x'], name, { type })

describe('sortPicks', () => {
  it('routes supported images by their declared type', () => {
    const { images, documents, rejected } = sortPicks([
      file('rcpa.png', 'image/png'),
      file('board.webp', 'image/webp'),
    ])
    expect(images?.map((f) => f.name)).toEqual(['rcpa.png', 'board.webp'])
    expect(documents).toEqual([])
    expect(rejected).toEqual([])
  })

  it('falls back to the extension when the browser reports no type', () => {
    // Android and some desktop pickers hand over an empty `type`.
    const { images, rejected } = sortPicks([file('visit.JPEG'), file('sheet.jpg')])
    expect(images).toHaveLength(2)
    expect(rejected).toEqual([])
  })

  it('routes pdf and docx to the Library pile', () => {
    const { images, documents } = sortPicks([
      file('cardevia-detailing-guide.pdf', 'application/pdf'),
      file('notes.docx'),
    ])
    expect(documents).toHaveLength(2)
    expect(images).toEqual([])
  })

  it('REJECTS anything else instead of calling it an image', () => {
    // The bug: the old split was "not a .pdf/.docx, therefore an image", so each
    // of these became an <img> with an object URL that could never decode.
    const { images, documents, rejected } = sortPicks([
      file('passport.heic', 'image/heic'),
      file('scan.tiff', 'image/tiff'),
      file('book.epub'),
      file('archive.zip', 'application/zip'),
    ])
    expect(images).toEqual([])
    expect(documents).toEqual([])
    expect(rejected?.map((f) => f.name)).toEqual([
      'passport.heic',
      'scan.tiff',
      'book.epub',
      'archive.zip',
    ])
  })

  it('keeps the good picks from a mixed selection', () => {
    const { images, documents, rejected } = sortPicks([
      file('rcpa.png', 'image/png'),
      file('passport.heic', 'image/heic'),
      file('sop.pdf', 'application/pdf'),
    ])
    expect(images?.map((f) => f.name)).toEqual(['rcpa.png'])
    expect(documents?.map((f) => f.name)).toEqual(['sop.pdf'])
    expect(rejected?.map((f) => f.name)).toEqual(['passport.heic'])
  })
})

describe('unsupportedMessage', () => {
  it('is null when nothing was rejected, so no banner appears', () => {
    expect(unsupportedMessage([])).toBeNull()
  })

  it('names the files, because "some were skipped" makes the rep guess', () => {
    const message = unsupportedMessage([file('passport.heic'), file('scan.tiff')])
    expect(message).toContain('passport.heic')
    expect(message).toContain('scan.tiff')
    expect(message).toContain('HEIC')
  })
})

describe('the accept attribute', () => {
  it('is derived from the same list the check uses, so they cannot disagree', () => {
    for (const mime of IMAGE_MIMES) expect(ACCEPT_ATTRIBUTE).toContain(mime)
    expect(ACCEPT_ATTRIBUTE).toContain('.pdf')
    expect(ACCEPT_ATTRIBUTE).toContain('.docx')
  })
})
