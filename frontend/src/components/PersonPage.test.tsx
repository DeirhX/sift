import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import PersonPage from './PersonPage'
import PeopleView from './PeopleView'
import Dialogs from './Dialogs'
import * as api from '../api'
import type { Person, PersonFace, PersonFacesResponse } from '../api/types'

vi.mock('../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api')>(),
  fetchPersonFaces: vi.fn(),
  assignFaces: vi.fn(),
  deleteFaces: vi.fn(),
  mergeClusters: vi.fn(),
  renameCluster: vi.fn(),
}))

const person = (cluster_id: number, name: string | null, faces: number): Person => ({
  cluster_id, name, faces, photos: faces, manual: false, cover_face_id: cluster_id + 100, cover_hash: 'h',
})

const face = (id: number, overrides: Partial<PersonFace> = {}): PersonFace => ({
  id, image_id: id, hash: `h${id}`, filename: `f${id}.jpg`, path: `/x/f${id}.jpg`,
  bbox: [10, 10, 50, 50], imgw: 400, imgh: 300, prob: 0.99, sharp: 0.8, expr: null,
  cluster_id: 0, rel: 0.1, quality: 0.7, capture_time: null, decision: null, match: 0.8,
  ...overrides,
})

const PEOPLE = [person(0, 'Aja', 3), person(1, null, 2), person(2, null, 1)]

function response(overrides: Partial<PersonFacesResponse> = {}): PersonFacesResponse {
  return {
    cluster_id: 0, name: 'Aja', total: 3, offset: 0, limit: 1000, has_match: true,
    items: [face(1), face(2), face(3, { match: 0.1 })],
    similar: [{ ...person(1, null, 2), similarity: 0.62 }],
    ...overrides,
  }
}

function renderPage(props: Partial<Parameters<typeof PersonPage>[0]> = {}) {
  const handlers = { onBack: vi.fn(), onOpenPerson: vi.fn(), onShowPhotos: vi.fn(), onChanged: vi.fn() }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <PersonPage clusterId={0} people={PEOPLE} {...handlers} {...props} />
      <Dialogs />
    </QueryClientProvider>,
  )
  return handlers
}

beforeEach(() => {
  vi.mocked(api.fetchPersonFaces).mockReset().mockResolvedValue(response())
  vi.mocked(api.assignFaces).mockReset().mockResolvedValue({ ok: true, cluster_id: 1, moved: 2 })
  vi.mocked(api.deleteFaces).mockReset().mockResolvedValue({ ok: true, deleted: 1 })
  vi.mocked(api.mergeClusters).mockReset().mockResolvedValue({ ok: true, moved: 2 })
})

describe('PersonPage', () => {
  it('moves the selected faces to another person', async () => {
    const handlers = renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Select face from f1.jpg' }))
    await userEvent.click(screen.getByRole('button', { name: 'Select face from f2.jpg' }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Move selected faces to'), '1')
    await waitFor(() => expect(api.assignFaces).toHaveBeenCalledWith([1, 2], { cluster_id: 1 }))
    await waitFor(() => expect(handlers.onChanged).toHaveBeenCalled())
    expect(handlers.onBack).not.toHaveBeenCalled()       // one face is still here
  })

  it('shift-click selects a range, and removing every face leaves the page', async () => {
    const handlers = renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Select face from f1.jpg' }))
    fireEvent.click(screen.getByRole('button', { name: 'Select face from f3.jpg' }), { shiftKey: true })
    expect(screen.getByText('3 selected')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Not a face' }))
    expect(screen.getByRole('alertdialog', { name: 'Remove 3 face boxes?' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(api.deleteFaces).toHaveBeenCalledWith([1, 2, 3]))
    await waitFor(() => expect(handlers.onBack).toHaveBeenCalled())
  })

  it('flags the face that looks unlike the rest', async () => {
    renderPage()
    await screen.findByRole('button', { name: 'Select face from f3.jpg' })
    expect(document.querySelectorAll('.face-tile.odd')).toHaveLength(1)
  })

  it('offers look-alike people to merge in', async () => {
    renderPage()
    expect(await screen.findByText('62%')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Merge in' }))
    await userEvent.click(screen.getByRole('button', { name: 'Merge' }))
    await waitFor(() => expect(api.mergeClusters).toHaveBeenCalledWith(1, 0))
  })
})

describe('PeopleView', () => {
  it('keeps unnamed people seen once out of the way until asked', async () => {
    const onOpen = vi.fn()
    render(<PeopleView people={PEOPLE} loading={false} onOpen={onOpen} />)
    expect(screen.getByRole('button', { name: /Aja/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Person 1/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Person 2/ })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Show 1 person seen only once' }))
    await userEvent.click(screen.getByRole('button', { name: /Person 2/ }))
    expect(onOpen).toHaveBeenCalledWith(2)
  })
})
