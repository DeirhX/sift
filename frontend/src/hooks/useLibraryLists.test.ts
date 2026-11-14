import { describe, it, expect } from 'vitest'
import { nextOffset } from './useLibraryLists'

describe('nextOffset', () => {
  it('advances by the page size until the total is reached', () => {
    expect(nextOffset({ offset: 0, limit: 60, total: 150 })).toBe(60)
    expect(nextOffset({ offset: 60, limit: 60, total: 150 })).toBe(120)
    expect(nextOffset({ offset: 120, limit: 60, total: 150 })).toBeUndefined()
    expect(nextOffset({ offset: 0, limit: 60, total: 60 })).toBeUndefined()
    expect(nextOffset({ offset: 0, limit: 60, total: 0 })).toBeUndefined()
  })
})
