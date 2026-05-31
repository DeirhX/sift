import { describe, it, expect } from 'vitest'
import { fmt, aestheticScore, groupByDup, fmtTimeRange } from './format.js'

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
})
