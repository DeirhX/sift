import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useDecisions } from './useDecisions'
import * as api from '../api'
import { clearToasts } from '../toast'
import type { ImageItem } from '../api/types'

vi.mock('../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api')>(),
  setDecision: vi.fn(),
}))

const item = (decision: string | null): ImageItem => ({
  id: 7, filename: 'a.jpg', path: '/x/a.jpg', hash: 'h7', decision, faces: [], tags: [],
} as unknown as ImageItem)

function setup() {
  const qc = new QueryClient()
  qc.setQueryData(['images', {}], { pages: [{ items: [item('keep')] }] })
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  const hook = renderHook(() => useDecisions(), { wrapper })
  const cached = () => (qc.getQueryData(['images', {}]) as { pages: { items: ImageItem[] }[] }).pages[0].items[0].decision
  return { ...hook, cached }
}

beforeEach(() => { vi.mocked(api.setDecision).mockReset().mockResolvedValue(undefined) })
afterEach(() => act(() => clearToasts()))

describe('useDecisions undo', () => {
  it('undoes single marks newest-first, restoring what was there before', async () => {
    const { result, cached } = setup()
    await act(() => result.current.setDecision(item('keep'), 'del'))     // keep -> del
    await act(() => result.current.setDecision(item('del'), 'del'))      // del -> cleared
    expect(cached()).toBeNull()

    await act(async () => { expect(await result.current.undoLastMark()).toBe(true) })
    expect(cached()).toBe('del')
    expect(api.setDecision).toHaveBeenLastCalledWith('h7', 'del', 7, '/x/a.jpg')

    await act(async () => { await result.current.undoLastMark() })
    expect(cached()).toBe('keep')
    expect(api.setDecision).toHaveBeenLastCalledWith('h7', 'keep', 7, '/x/a.jpg')

    await act(async () => { expect(await result.current.undoLastMark()).toBe(false) })
  })

  it('does not offer to undo a mark that never saved', async () => {
    vi.mocked(api.setDecision).mockRejectedValueOnce(new Error('db is read-only'))
    const { result, cached } = setup()
    await act(() => result.current.setDecision(item('keep'), 'del'))
    expect(cached()).toBe('keep')                                         // reverted
    await act(async () => { expect(await result.current.undoLastMark()).toBe(false) })
    expect(api.setDecision).toHaveBeenCalledTimes(1)
  })
})
