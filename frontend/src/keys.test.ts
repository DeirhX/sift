import { describe, it, expect } from 'vitest'
import { decisionForKey, hasModifier, isTypingTarget } from './keys'

describe('decisionForKey', () => {
  it('maps the keep keys', () => {
    for (const key of ['k', 'K', '+', '=']) expect(decisionForKey({ key })).toBe('keep')
  })

  it('maps the delete keys', () => {
    for (const key of ['d', 'D', '-', 'Delete']) expect(decisionForKey({ key })).toBe('del')
  })

  it('ignores unrelated keys', () => {
    expect(decisionForKey({ key: 'x' })).toBeNull()
    expect(decisionForKey({ key: 'Enter' })).toBeNull()
  })

  it('never treats a browser chord as a verdict', () => {
    // Ctrl+- (zoom out), Ctrl+D (bookmark), Cmd+K, Alt+D (address bar)
    expect(decisionForKey({ key: '-', ctrlKey: true })).toBeNull()
    expect(decisionForKey({ key: 'd', ctrlKey: true })).toBeNull()
    expect(decisionForKey({ key: 'k', metaKey: true })).toBeNull()
    expect(decisionForKey({ key: 'd', altKey: true })).toBeNull()
    expect(decisionForKey({ key: '=', ctrlKey: true })).toBeNull()
  })
})

describe('hasModifier', () => {
  it('is false for a plain or shifted key', () => {
    expect(hasModifier({ key: 'k' })).toBe(false)
  })
})

describe('isTypingTarget', () => {
  it('detects form controls', () => {
    expect(isTypingTarget(document.createElement('input'))).toBe(true)
    expect(isTypingTarget(document.createElement('select'))).toBe(true)
    expect(isTypingTarget(document.createElement('div'))).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
  })
})
