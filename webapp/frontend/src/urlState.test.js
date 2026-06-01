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

  it('parses the grid lightbox (img only)', () => {
    expect(parseState('?img=42').nav).toEqual({ kind: 'lightbox', imgId: 42 })
  })

  it('parses a group review with focused image + zoom', () => {
    expect(parseState('?view=groups&grp=5&img=7&zoom=1').nav)
      .toEqual({ kind: 'group', refId: 5, imgId: 7, zoom: true })
  })

  it('parses a scene review without a focused image', () => {
    expect(parseState('?view=scenes&scn=3').nav)
      .toEqual({ kind: 'scene', refId: 3, imgId: null, zoom: false })
  })

  it('prefers grp over scn over img and ignores bad numbers', () => {
    expect(parseState('?grp=2&scn=3&img=4').nav.kind).toBe('group')
    expect(parseState('?grp=abc&img=9').nav).toEqual({ kind: 'lightbox', imgId: 9 })
    expect(parseState('').nav).toBe(null)
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

  it('encodes overlay nav and round-trips it', () => {
    expect(buildSearch(DEFAULT_FILTERS, 'grid', { kind: 'lightbox', imgId: 8 })).toBe('?img=8')
    const nav = { kind: 'group', refId: 5, imgId: 7, zoom: true }
    expect(parseState(buildSearch(DEFAULT_FILTERS, 'groups', nav)).nav).toEqual(nav)
  })

  it('omits img when there is no focused image, and zoom unless set', () => {
    expect(buildSearch(DEFAULT_FILTERS, 'scenes', { kind: 'scene', refId: 3, imgId: null, zoom: false }))
      .toBe('?view=scenes&scn=3')
  })
})
