import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Toasts from './Toasts'
import ShortcutHelp from './ShortcutHelp'
import { toast, clearToasts } from '../toast'

afterEach(() => act(() => clearToasts()))

describe('Toasts', () => {
  it('announces an error and lets it be dismissed', async () => {
    render(<Toasts />)
    expect(screen.queryByRole('alert')).toBeNull()
    act(() => { toast.error('Couldn’t save the mark for a.jpg.') })
    expect(screen.getByRole('alert')).toHaveTextContent('Couldn’t save the mark for a.jpg.')
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('ShortcutHelp', () => {
  it('lists the shortcuts and closes on Escape', async () => {
    let closed = 0
    render(<ShortcutHelp onClose={() => { closed++ }} />)
    expect(screen.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeInTheDocument()
    expect(screen.getByText('Jump to search')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(closed).toBe(1)
  })
})
