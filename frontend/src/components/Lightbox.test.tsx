import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Lightbox from './Lightbox'
import type { ImageItem } from '../api/types'

vi.mock('../api', async (original) => ({
  ...(await original<typeof import('../api')>()),
  fetchLocations: vi.fn().mockResolvedValue({ count: 1, locations: [] }),
  fetchMeta: vi.fn().mockResolvedValue({ photo_roots: [] }),
}))

const item = (id: number): ImageItem => ({
  id, filename: `p${id}.jpg`, path: `/x/p${id}.jpg`, hash: `h${id}`,
  combined: 0.8, sharpness: 0.7, para_aesthetic: 0.75, clip_iqa: null,
  imgw: 400, imgh: 300, dup_group: null, decision: null, caption: null,
  faces: [], tags: [],
} as unknown as ImageItem)

async function open(onDecision = vi.fn(), setIndex = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <Lightbox items={[item(1), item(2), item(3)]} index={1} setIndex={setIndex} onDecision={onDecision} />
    </QueryClientProvider>,
  )
  // Let the locations/meta lookups settle so later assertions see a quiet tree.
  await act(async () => {})
  return { onDecision, setIndex }
}

describe('Lightbox', () => {
  it('is a labelled dialog that says where you are', async () => {
    await open()
    expect(screen.getByRole('dialog', { name: /p2\.jpg/ })).toBeInTheDocument()
    expect(screen.getByText(/Photo 2 of 3/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous photo' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next photo' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close viewer' })).toBeInTheDocument()
  })

  it('marks with plain keys, in either case', async () => {
    const { onDecision } = await open()
    fireEvent.keyDown(window, { key: 'k' })
    fireEvent.keyDown(window, { key: 'D' })
    expect(onDecision.mock.calls.map((c) => c[1])).toEqual(['keep', 'del'])
  })

  it('ignores browser chords such as Ctrl+- and Ctrl+D', async () => {
    const { onDecision, setIndex } = await open()
    fireEvent.keyDown(window, { key: '-', ctrlKey: true })
    fireEvent.keyDown(window, { key: 'd', ctrlKey: true })
    fireEvent.keyDown(window, { key: 'k', metaKey: true })
    fireEvent.keyDown(window, { key: 'ArrowLeft', altKey: true })
    expect(onDecision).not.toHaveBeenCalled()
    expect(setIndex).not.toHaveBeenCalled()
  })

  it('shows the thumbnail until the full-size photo has loaded', async () => {
    await open()
    const full = screen.getByAltText('p2.jpg')
    expect(full).toHaveClass('loading')
    expect(document.querySelector('.lb-placeholder')).not.toBeNull()
    fireEvent.load(full)
    expect(full).not.toHaveClass('loading')
    expect(document.querySelector('.lb-placeholder')).toBeNull()
  })
})
