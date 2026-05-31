import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DecideButtons from './DecideButtons.jsx'

describe('DecideButtons', () => {
  it('marks the active decision', () => {
    render(<DecideButtons item={{ decision: 'keep' }} onDecision={() => {}} />)
    expect(screen.getByRole('button', { name: 'Keep' })).toHaveClass('active')
    expect(screen.getByRole('button', { name: 'Delete' })).not.toHaveClass('active')
  })

  it('fires onDecision with the item and verdict', async () => {
    const item = { id: 3, decision: null }
    const onDecision = vi.fn()
    render(<DecideButtons item={item} onDecision={onDecision} />)
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onDecision).toHaveBeenCalledWith(item, 'del')
  })
})
