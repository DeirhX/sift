import { describe, it, expect } from 'vitest'
import { fmt, aestheticScore, groupByDup, fmtTimeRange, representative, repFirst, applyDecisionHide, isDeleted, applyTrashHide, sceneKeywords } from './format'

describe('fmt', () => {
  it('formats numbers to two decimals', () => {
    expect(fmt(0.8)).toBe('0.80')
    expect(fmt(0.123)).toBe('0.12')
  })

  it('renders an en-dash for null/undefined', () => {
    expect(fmt(null)).toBe('–')
    expect(fmt(undefined)).toBe('–')
  })

  it('treats 0 as a real value, not missing', () => {
    expect(fmt(0)).toBe('0.00')
  })
})

describe('aestheticScore', () => {
  it('prefers the PARA aesthetic score', () => {
    expect(aestheticScore({ para_aesthetic: 0.7, clip_iqa: 0.2 })).toBe(0.7)
  })

  it('falls back to clip_iqa when PARA is absent', () => {
    expect(aestheticScore({ para_aesthetic: null, clip_iqa: 0.42 })).toBe(0.42)
  })

  it('is undefined when neither score exists', () => {
    expect(aestheticScore({})).toBeUndefined()
  })
})

describe('groupByDup', () => {
  const items = [
    { id: 1, dup_group: 0 },
    { id: 2, dup_group: 0 },
    { id: 3, dup_group: null },
    { id: 4, dup_group: 1 },
    { id: 5, dup_group: null },
  ]

  it('splits items into near-dup sets and loose members', () => {
    const { sets, loose } = groupByDup(items)
    expect(sets.map((s) => s.dup_group)).toEqual([0, 1])
    expect(sets[0].items.map((i) => i.id)).toEqual([1, 2])
    expect(sets[1].items.map((i) => i.id)).toEqual([4])
    expect(loose.map((i) => i.id)).toEqual([3, 5])
  })

  it('orders sets by dup_group id ascending', () => {
    const out = groupByDup([{ id: 1, dup_group: 5 }, { id: 2, dup_group: 2 }])
    expect(out.sets.map((s) => s.dup_group)).toEqual([2, 5])
  })

  it('handles empty / nullish input', () => {
    expect(groupByDup([])).toEqual({ sets: [], loose: [] })
    expect(groupByDup(undefined)).toEqual({ sets: [], loose: [] })
  })

  it('puts everything in loose when no item has a dup_group', () => {
    const { sets, loose } = groupByDup([{ id: 1, dup_group: null }])
    expect(sets).toEqual([])
    expect(loose).toHaveLength(1)
  })
})

describe('representative / repFirst', () => {
  it('picks the most central member (highest dup_central)', () => {
    const list = [
      { id: 1, dup_central: 0.80, combined: 0.9 },
      { id: 2, dup_central: 0.95, combined: 0.5 },
      { id: 3, dup_central: 0.88, combined: 0.7 },
    ]
    expect(representative(list)!.id).toBe(2)
  })

  it('breaks centrality ties on quality', () => {
    const list = [
      { id: 1, dup_central: 0.9, combined: 0.6 },
      { id: 2, dup_central: 0.9, combined: 0.8 },
    ]
    expect(representative(list)!.id).toBe(2)
  })

  it('falls back to quality when centrality is absent', () => {
    const list = [
      { id: 1, combined: 0.6 },
      { id: 2, combined: 0.8 },
    ]
    expect(representative(list)!.id).toBe(2)
  })

  it('repFirst leads with the representative, keeps the rest in order', () => {
    const list = [
      { id: 1, dup_central: 0.80, combined: 0.9 },
      { id: 2, dup_central: 0.95, combined: 0.5 },
      { id: 3, dup_central: 0.88, combined: 0.7 },
    ]
    expect(repFirst(list).map((i) => i.id)).toEqual([2, 1, 3])
  })

  it('handles empty input', () => {
    expect(representative([])).toBeUndefined()
    expect(repFirst([])).toEqual([])
  })
})

describe('fmtTimeRange', () => {
  it('returns null when start is missing', () => {
    expect(fmtTimeRange(null, 1000)).toBeNull()
  })

  it('collapses to a single label when start equals end', () => {
    const out = fmtTimeRange(1000, 1000)
    expect(out).not.toContain('–')
  })

  it('shows a range when start and end differ', () => {
    // Two days apart guarantees distinct labels regardless of locale.
    const out = fmtTimeRange(1000, 1000 + 2 * 86400)
    expect(out).toContain('–')
  })

  it('drops the repeated date for a same-day range', () => {
    const start = 1593864000 // 2020-07-04 ~midday UTC: same local day everywhere
    const [head, tail] = (fmtTimeRange(start, start + 3600) ?? '').split(' – ')
    expect(tail).toBeTruthy()
    expect(tail.length).toBeLessThan(head.length)
    // The year is in the dated head but not the time-only tail (locale-agnostic).
    expect(head).toContain('2020')
    expect(tail).not.toContain('2020')
  })

  it('keeps both dates when the range spans days', () => {
    const start = 1593864000
    const [, tail] = (fmtTimeRange(start, start + 2 * 86400) ?? '').split(' – ')
    expect(tail).toContain('2020')
  })
})

describe('applyDecisionHide', () => {
  const items = [
    { id: 1, decision: 'keep' },
    { id: 2, decision: 'del' },
    { id: 3, decision: null },
  ]

  it('drops del-marked members under the "notdel" filter', () => {
    expect(applyDecisionHide(items, 'notdel').map((i) => i.id)).toEqual([1, 3])
  })

  it('is a no-op for any other decision value', () => {
    expect(applyDecisionHide(items, 'all')).toBe(items)
    expect(applyDecisionHide(items, 'keep')).toBe(items)
  })

  it('tolerates nullish input', () => {
    expect(applyDecisionHide(undefined, 'notdel')).toEqual([])
    expect(applyDecisionHide(null, 'all')).toEqual([])
  })
})

describe('isDeleted / applyTrashHide', () => {
  const items = [
    { id: 1, trash_state: null },
    { id: 2, trash_state: 'trashed' },
    { id: 3 },
    { id: 4, trash_state: 'emptied' },
  ]

  it('flags any non-null trash_state as deleted', () => {
    expect(items.filter(isDeleted).map((i) => i.id)).toEqual([2, 4])
  })

  it('drops trashed members when not showing deleted', () => {
    expect(applyTrashHide(items, false).map((i) => i.id)).toEqual([1, 3])
  })

  it('is a no-op when showing deleted', () => {
    expect(applyTrashHide(items, true)).toBe(items)
  })

  it('tolerates nullish input', () => {
    expect(applyTrashHide(undefined, false)).toEqual([])
    expect(applyTrashHide(null, true)).toEqual([])
  })
})

describe('sceneKeywords', () => {
  const items = [
    { tags: ['beach', 'sunset'] },
    { tags: ['beach', 'people'] },
    { tags: ['beach'] },
    { tags: ['sunset'] },
    { tags: [] },
    {},
  ]

  it('ranks tags by frequency, most common first', () => {
    const kw = sceneKeywords(items)
    expect(kw[0]).toEqual({ tag: 'beach', count: 3 })
    expect(kw[1]).toEqual({ tag: 'sunset', count: 2 })
    expect(kw.find((k) => k.tag === 'people')).toEqual({ tag: 'people', count: 1 })
  })

  it('breaks frequency ties alphabetically for stable ordering', () => {
    const kw = sceneKeywords([{ tags: ['zebra'] }, { tags: ['apple'] }])
    expect(kw.map((k) => k.tag)).toEqual(['apple', 'zebra'])
  })

  it('caps the result at the requested limit', () => {
    expect(sceneKeywords(items, 2).map((k) => k.tag)).toEqual(['beach', 'sunset'])
  })

  it('returns [] for empty / nullish input', () => {
    expect(sceneKeywords([])).toEqual([])
    expect(sceneKeywords(undefined)).toEqual([])
  })
})
