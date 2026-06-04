import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTaskStream } from './useTaskStream'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onopen: ((e: Event) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  private listeners = new Map<string, ((e: MessageEvent) => void)[]>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    const list = this.listeners.get(type) ?? []
    list.push(cb)
    this.listeners.set(type, list)
  }

  close = vi.fn()

  open() {
    this.onopen?.(new Event('open'))
  }

  fail() {
    this.onerror?.(new Event('error'))
  }

  emit(type: string, data: unknown, id = '') {
    const event = { data: JSON.stringify(data), lastEventId: id } as MessageEvent
    for (const cb of this.listeners.get(type) ?? []) cb(event)
  }
}

const runningTask = {
  id: 'task1',
  type: 'apply_decisions',
  state: 'running',
  commands: [],
}

describe('useTaskStream', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(runningTask),
    })))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('reconnects from the last seen SSE event id while the task is still running', async () => {
    renderHook(() => useTaskStream('task1'))

    const first = FakeEventSource.instances[0]
    act(() => {
      first.open()
      first.emit('line', 'hello', '3')
      first.fail()
    })

    await act(async () => {
      await Promise.resolve()
    })
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/tasks/task1')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })

    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1].url).toBe('/api/tasks/task1/stream?after=3')
  })
})

