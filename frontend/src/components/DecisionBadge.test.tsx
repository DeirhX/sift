import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import DecisionBadge from './DecisionBadge'

describe('DecisionBadge', () => {
  it('shows KEEP for a kept photo', () => {
    render(<DecisionBadge decision="keep" />)
    expect(screen.getByText('KEEP')).toBeInTheDocument()
  })

  it('shows DEL for a deleted photo', () => {
    render(<DecisionBadge decision="del" />)
    expect(screen.getByText('DEL')).toBeInTheDocument()
  })

  it('renders nothing when undecided', () => {
    const { container } = render(<DecisionBadge decision={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})
