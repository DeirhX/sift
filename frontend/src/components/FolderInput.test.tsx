import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FolderInput from './FolderInput'
import { fsComplete } from '../api'

vi.mock('../api', () => ({ fsComplete: vi.fn() }))
const mockFs = vi.mocked(fsComplete)

describe('FolderInput', () => {
  beforeEach(() => mockFs.mockReset())

  it('lists directory suggestions on focus, by basename', async () => {
    mockFs.mockResolvedValue({ entries: ['C:\\Users', 'C:\\Windows'], truncated: false })
    render(<FolderInput value={'C:\\'} onChange={() => {}} />)

    await userEvent.click(screen.getByRole('textbox'))

    expect(await screen.findByText('Users')).toBeInTheDocument()
    expect(screen.getByText('Windows')).toBeInTheDocument()
    expect(mockFs).toHaveBeenCalledWith('C:\\')
  })

  it('appends a separator when a folder is picked, so the next lookup drills in', async () => {
    mockFs.mockResolvedValue({ entries: ['C:\\Users'], truncated: false })
    const onChange = vi.fn()
    render(<FolderInput value={'C:\\'} onChange={onChange} />)

    await userEvent.click(screen.getByRole('textbox'))
    await userEvent.click(await screen.findByText('Users'))

    expect(onChange).toHaveBeenCalledWith('C:\\Users\\')   // Windows separator preserved
  })

  it('keyboard: ArrowDown + Enter selects the highlighted folder (unix sep)', async () => {
    mockFs.mockResolvedValue({ entries: ['/home/a', '/home/b'], truncated: false })
    const onChange = vi.fn()
    render(<FolderInput value="/home/" onChange={onChange} />)

    await userEvent.click(screen.getByRole('textbox'))
    await screen.findByText('a')
    await userEvent.keyboard('{ArrowDown}{Enter}')

    expect(onChange).toHaveBeenCalledWith('/home/a/')
  })

  it('surfaces a truncation hint when results are capped', async () => {
    mockFs.mockResolvedValue({ entries: ['/x/1'], truncated: true })
    render(<FolderInput value="/x/" onChange={() => {}} />)

    await userEvent.click(screen.getByRole('textbox'))

    expect(await screen.findByText(/more/i)).toBeInTheDocument()
  })
})
