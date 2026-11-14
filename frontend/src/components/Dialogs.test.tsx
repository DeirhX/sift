import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Dialogs from './Dialogs'
import { confirmDialog, promptDialog, dismissAllDialogs } from '../dialog'

afterEach(() => act(() => dismissAllDialogs()))

function ask<T>(fn: () => Promise<T>): Promise<T> {
  let p!: Promise<T>
  act(() => { p = fn() })
  return p
}

describe('Dialogs', () => {
  it('confirms with the labelled button', async () => {
    render(<Dialogs />)
    const answer = ask(() => confirmDialog({ title: 'Move 3 photos to Trash?', detail: 'E:\\lib\\_trash', confirmLabel: 'Move to Trash' }))
    const dlg = screen.getByRole('alertdialog', { name: 'Move 3 photos to Trash?' })
    expect(dlg).toHaveTextContent('E:\\lib\\_trash')
    await userEvent.click(screen.getByRole('button', { name: 'Move to Trash' }))
    expect(await answer).toBe(true)
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('cancels on Escape and on a backdrop click', async () => {
    render(<Dialogs />)
    const first = ask(() => confirmDialog({ title: 'A?' }))
    await userEvent.keyboard('{Escape}')
    expect(await first).toBe(false)
    const second = ask(() => confirmDialog({ title: 'B?' }))
    await userEvent.click(document.querySelector('.dialog-overlay')!)
    expect(await second).toBe(false)
  })

  it('Enter confirms an ordinary dialog but never a dangerous one', async () => {
    render(<Dialogs />)
    const safe = ask(() => confirmDialog({ title: 'Restore?' }))
    fireEvent.keyDown(document.body, { key: 'Enter' })
    expect(await safe).toBe(true)

    const scary = ask(() => confirmDialog({ title: 'Delete forever?', danger: true, confirmLabel: 'Delete permanently' }))
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    fireEvent.keyDown(document.body, { key: 'Enter' })
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete permanently' }))
    expect(await scary).toBe(true)
  })

  it('keeps key presses away from whatever is behind it', async () => {
    const behind = vi.fn()
    window.addEventListener('keydown', behind)
    render(<Dialogs />)
    const answer = ask(() => confirmDialog({ title: 'Sure?' }))
    fireEvent.keyDown(document.body, { key: 'd' })
    expect(behind).not.toHaveBeenCalled()
    await userEvent.keyboard('{Escape}')
    await answer
    window.removeEventListener('keydown', behind)
  })

  it('prompts for text and returns null when cancelled', async () => {
    render(<Dialogs />)
    const named = ask(() => promptDialog({ title: 'New person', label: 'Name', confirmLabel: 'Create' }))
    await userEvent.type(screen.getByLabelText('Name'), 'Aja{Enter}')
    expect(await named).toBe('Aja')
    const cancelled = ask(() => promptDialog({ title: 'New person', label: 'Name' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await cancelled).toBeNull()
  })

  it('shows queued dialogs one at a time', async () => {
    render(<Dialogs />)
    const a = ask(() => confirmDialog({ title: 'First?' }))
    const b = ask(() => confirmDialog({ title: 'Second?' }))
    expect(screen.getAllByRole('alertdialog')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'OK' }))
    expect(await a).toBe(true)
    expect(screen.getByRole('alertdialog', { name: 'Second?' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await b).toBe(false)
  })
})
