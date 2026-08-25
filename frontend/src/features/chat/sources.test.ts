// extractSources parses tool output — a shape the frontend never controls, so
// the malformed cases are the point, not the happy path.

import { describe, expect, it } from 'vitest'

import type { ToolCall } from '@/lib/types'

import { extractSources } from './sources'

const call = (name: string, output?: string): ToolCall => ({
  callId: 'c1',
  name,
  input: {},
  output,
  status: 'done',
})

const row = (document: string, section = '4.2', page = 3) =>
  JSON.stringify({ rows: [{ document, section, page, doc_type: 'monograph' }] })

describe('extractSources', () => {
  it('reads document, section, page and doc_type from search_literature rows', () => {
    expect(extractSources([call('search_literature', row('Cardevia SmPC'))])).toEqual([
      { document: 'Cardevia SmPC', section: '4.2', page: 3, docType: 'monograph' },
    ])
  })

  it('ignores other tools even when their output looks right', () => {
    expect(extractSources([call('run_sql', row('Cardevia SmPC'))])).toEqual([])
  })

  it.each([
    ['malformed JSON', 'not json {'],
    ['no output', undefined],
    ['rows not an array', '{"rows": {"document": "X"}}'],
    ['row without a document', '{"rows": [{"section": "4.2"}]}'],
    ['row is not an object', '{"rows": ["X"]}'],
    ['top level not an object', '"X"'],
  ])('survives %s and returns nothing', (_label, output) => {
    expect(extractSources([call('search_literature', output)])).toEqual([])
  })

  it('deduplicates on document+section, keeping the first occurrence', () => {
    const out = JSON.stringify({
      rows: [
        { document: 'A', section: '4.2', page: 1 },
        { document: 'A', section: '4.2', page: 9 }, // same key, later page
        { document: 'A', section: '4.5', page: 2 },
      ],
    })
    expect(extractSources([call('search_literature', out)])).toEqual([
      { document: 'A', section: '4.2', page: 1, docType: null },
      { document: 'A', section: '4.5', page: 2, docType: null },
    ])
  })

  it('treats a missing section as front matter (empty), not a crash', () => {
    const out = JSON.stringify({ rows: [{ document: 'A', page: null }] })
    expect(extractSources([call('search_literature', out)])).toEqual([
      { document: 'A', section: '', page: null, docType: null },
    ])
  })
})
