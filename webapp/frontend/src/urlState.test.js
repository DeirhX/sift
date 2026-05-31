import { describe, it, expect } from 'vitest'
import { DEFAULT_FILTERS, parseState, buildSearch } from './urlState.js'

describe('parseState', () => {
  it('returns defaults for an empty query', () => {
    const { filters, view } = parseState('')
    expect(filters).toEqual(DEFAULT_FILTERS)
    expect(view).toBe('grid')
  })

  it('parses numbers, strings and lists', () => {
    const { filters } = parseState('?sort=portrait&scoreMin=0.3&tags=a,b&people=1,2')
    expect(filters.sort).toBe('portrait')
    expect(filters.scoreMin).toBe(0.3)
    expect(filters.tags).toEqual(['a', 'b'])
    expect(filters.people).toEqual(['1', '2'])
  })

  it('ignores non-numeric values for numeric keys', () => {
    expect(parseState('?scoreMin=abc').filters.scoreMin).toBe(DEFAULT_FILTERS.scoreMin)
  })

  it('reads the groups view', () => {
    expect(parseState('?view=groups').view).toBe('groups')
    expect(parseState('?view=bogus').view).toBe('grid')
  })
})

describe('buildSearch', () => {
  it('omits default values', () => {
    expect(buildSearch(DEFAULT_FILTERS, 'grid')).toBe('')
  })

  it('encodes the view only when not grid', () => {
    expect(buildSearch(DEFAULT_FILTERS, 'groups')).toBe('?view=groups')
  })

  it('round-trips non-default filters', () => {
    const filters = { ...DEFAULT_FILTERS, sort: 'portrait', scoreMin: 0.5, tags: ['x'], people: ['7'] }
    const round = parseState(buildSearch(filters, 'grid')).filters
    expect(round.sort).toBe('portrait')
    expect(round.scoreMin).toBe(0.5)
    expect(round.tags).toEqual(['x'])
    expect(round.people).toEqual(['7'])
  })
})
