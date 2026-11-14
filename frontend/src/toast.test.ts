import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { toast, useToasts, dismissToast, clearToasts, errorText } from './toast'

describe('toast store', () => {
  beforeEach(() => { vi.useFakeTimers(); clearToasts() })
  afterEach(() => { act(() => clearToasts()); vi.useRealTimers() })

  it('shows a toast and expires it', () => {
    const { result } = renderHook(() => useToasts())
    act(() => { toast.error('Save failed') })
    expect(result.current.map((t) => t.message)).toEqual(['Save failed'])
    act(() => { vi.advanceTimersByTime(8000) })
    expect(result.current).toHaveLength(0)
  })

  it('collapses a repeated message instead of stacking it', () => {
    const { result } = renderHook(() => useToasts())
    act(() => { toast.error('Same'); toast.error('Same') })
    expect(result.current).toHaveLength(1)
  })

  it('caps how many are visible and can be dismissed', () => {
    const { result } = renderHook(() => useToasts())
    act(() => { for (let i = 0; i < 6; i++) toast.info(`m${i}`) })
    expect(result.current.map((t) => t.message)).toEqual(['m2', 'm3', 'm4', 'm5'])
    act(() => { dismissToast(result.current[0].id) })
    expect(result.current.map((t) => t.message)).toEqual(['m3', 'm4', 'm5'])
  })

  it('describes errors in plain words', () => {
    expect(errorText(new TypeError('Failed to fetch'))).toMatch(/reach the Sift server/)
    expect(errorText(new Error('photo is locked'))).toBe('photo is locked')
    expect(errorText('nope')).toBe('Something went wrong.')
    act(() => { toast.failure('Couldn’t save the mark.', new Error('db is read-only')) })
  })
})
