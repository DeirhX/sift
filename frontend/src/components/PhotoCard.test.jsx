import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PhotoCard from './PhotoCard.jsx'

function makeItem(overrides = {}) {
  return {
    id: 1, filename: 'a.jpg', path: '/x/a.jpg', hash: 'h1',
    combined: 0.8, sharpness: 0.7, para_aesthetic: 0.75, clip_iqa: null,
    imgw: 400, imgh: 300, dup_group: 0, decision: null, caption: 'a nice photo',
    faces: [{ id: 10, bbox: [10, 10, 50, 50], prob: 0.99, cluster_id: 0, sharp: 0.9, expr: 0.8 }],
    tags: ['sunset'],
    ...overrides,
  }
}

const baseProps = {
  colWidth: 200, thumbH: 150, onOpen: () => {},
  personName: (cid) => `Person ${cid}`, people: [{ cluster_id: 0, name: 'Bob', count: 2 }],
  onFaceChange: () => {},
}

describe('PhotoCard', () => {
  it('renders formatted scores', () => {
    render(<PhotoCard item={makeItem()} onDecision={() => {}} {...baseProps} />)
    expect(screen.getByText('Q 0.80')).toBeInTheDocument()  // composite quality pill
    expect(screen.getByText('0.70')).toBeInTheDocument()    // sharpness
    expect(screen.getByText('0.75')).toBeInTheDocument()    // aesthetic
  })

  it('shows the duplicate-group badge', () => {
    render(<PhotoCard item={makeItem()} onDecision={() => {}} {...baseProps} />)
    expect(screen.getByText('dup #0')).toBeInTheDocument()
  })

  it('renders a face overlay box per detected face', () => {
    const { container } = render(<PhotoCard item={makeItem()} onDecision={() => {}} {...baseProps} />)
    expect(container.querySelectorAll('.face-box')).toHaveLength(1)
  })

  it('fires onDecision when Keep/Delete are clicked', async () => {
    const item = makeItem()
    const onDecision = vi.fn()
    render(<PhotoCard item={item} onDecision={onDecision} {...baseProps} />)
    await userEvent.click(screen.getByRole('button', { name: 'Keep' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onDecision).toHaveBeenNthCalledWith(1, item, 'keep')
    expect(onDecision).toHaveBeenNthCalledWith(2, item, 'del')
  })

  it('surfaces the decision badge once decided', () => {
    render(<PhotoCard item={makeItem({ decision: 'del' })} onDecision={() => {}} {...baseProps} />)
    expect(screen.getByText('DEL')).toBeInTheDocument()
  })
})
