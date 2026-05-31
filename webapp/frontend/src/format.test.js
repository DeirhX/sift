import { describe, it, expect } from 'vitest'
import { fmt, aestheticScore } from './format.js'

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
