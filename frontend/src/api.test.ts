import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { buildImagesQuery, setDecision, fetchMeta, assignFace } from './api'
import { DEFAULT_FILTERS } from './urlState'

describe('buildImagesQuery', () => {
  it('serializes paging, sort and filter params', () => {
    const qs = buildImagesQuery(DEFAULT_FILTERS, 0, 60)
    const p = new URLSearchParams(qs)
    expect(p.get('offset')).toBe('0')
    expect(p.get('limit')).toBe('60')
    expect(p.get('sort')).toBe('combined')
    expect(p.get('dir')).toBe('desc')
    expect(p.get('dup_mode')).toBe('all')
    expect(p.get('score_min')).toBe('0')
  })

  it('only emits portrait bounds when narrowed', () => {
    const wide = new URLSearchParams(buildImagesQuery(DEFAULT_FILTERS, 0, 60))
    expect(wide.has('portrait_min')).toBe(false)
    const narrowed = new URLSearchParams(
      buildImagesQuery({ ...DEFAULT_FILTERS, portraitMin: 0.4 }, 0, 60))
    expect(narrowed.get('portrait_min')).toBe('0.4')
  })

  it('joins tags and people as CSV', () => {
    const p = new URLSearchParams(
      buildImagesQuery({ ...DEFAULT_FILTERS, tags: ['a', 'b'], people: ['1'] }, 0, 60))
    expect(p.get('tags')).toBe('a,b')
    expect(p.get('people')).toBe('1')
  })
})

describe('fetch wrappers', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })))
  })
  afterEach(() => vi.unstubAllGlobals())

  it('setDecision POSTs a JSON body', async () => {
    await setDecision('hash123', 'keep')
    const [url, opts] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('/api/decisions')
    expect(opts?.method).toBe('POST')
    expect(JSON.parse(opts?.body as string)).toEqual({ hash: 'hash123', decision: 'keep' })
  })

  it('assignFace posts to the face endpoint and returns JSON', async () => {
    const res = await assignFace(5, { cluster_id: 2 })
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/faces/5/assign')
    expect(res).toEqual({ ok: true })
  })

  it('jsonFetch throws on a non-ok response', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false })))
    await expect(fetchMeta()).rejects.toThrow()
  })

  it('jsonFetch surfaces FastAPI detail messages', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: 'a task is already running' }),
    })))
    await expect(fetchMeta()).rejects.toThrow('a task is already running')
  })
})
