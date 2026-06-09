import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import WindowedPileGrid from './WindowedPileGrid'

// jsdom has no layout engine, ResizeObserver or scrollTo. Stub a fixed viewport
// so the windowing math has real dimensions to work with (900x600 → 3 columns).
beforeAll(() => {
  class RO { observe() {} unobserve() {} disconnect() {} }
  vi.stubGlobal('ResizeObserver', RO as unknown as typeof ResizeObserver)
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 900 })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, value: 600 })
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: () => {} })
})

interface Row { id: number }

function renderGrid(count: number, extra: Partial<React.ComponentProps<typeof WindowedPileGrid<Row>>> = {}) {
  const items: Row[] = Array.from({ length: count }, (_, i) => ({ id: i }))
  return render(
    <WindowedPileGrid<Row>
      items={items}
      getKey={(r) => r.id}
      metaHeight={44}
      cellMinWidth={220}
      hasNextPage={false}
      isFetchingNextPage={false}
      fetchNextPage={() => {}}
      onActivate={() => {}}
      renderCell={(r, i, focused) => (
        <div className={'cell' + (focused ? ' focused' : '')} data-idx={i}>{r.id}</div>
      )}
      {...extra}
    />,
  )
}

describe('WindowedPileGrid', () => {
  it('mounts only a bounded subset of cells for a huge list', () => {
    const { container } = renderGrid(1000)
    const mounted = container.querySelectorAll('.cell')
    // 900px → 3 cols; 600px viewport + 600px overscan over ~322px rows ≈ a few
    // rows. Far below the full 1000; assert it never explodes.
    expect(mounted.length).toBeGreaterThan(0)
    expect(mounted.length).toBeLessThan(60)
  })

  it('reserves full scroll height for all rows via the spacer', () => {
    const { container } = renderGrid(1000)
    const spacer = container.querySelector('.grid-scroll > div') as HTMLElement
    // 1000 items / 3 cols = 334 rows → a tall spacer even though few cells mount.
    expect(parseInt(spacer.style.height, 10)).toBeGreaterThan(10000)
  })

  // The core stall-proofing proof: the mounted slice must FOLLOW the viewport,
  // not just render a fixed top window. A naive "render first N rows" impl would
  // pass the bounded-mount test above but fail this one.
  it('tracks the viewport: scrolling mounts a different, still-bounded slice', () => {
    const { container, getByRole } = renderGrid(1000)
    const grid = getByRole('grid')
    expect(container.querySelector('.cell[data-idx="0"]')).not.toBeNull()

    Object.defineProperty(grid, 'scrollTop', { configurable: true, value: 34800 })
    fireEvent.scroll(grid)

    const mounted = container.querySelectorAll('.cell')
    expect(mounted.length).toBeLessThan(60)                       // still bounded
    expect(container.querySelector('.cell[data-idx="0"]')).toBeNull()  // top unmounted
    const idxs = [...mounted].map((e) => Number(e.getAttribute('data-idx')))
    expect(Math.max(...idxs)).toBeGreaterThan(250)               // deep rows mounted
  })

  it('keeps the focused cell mounted even when scrolled far away', () => {
    const { container, getByRole } = renderGrid(1000)
    const grid = getByRole('grid')
    fireEvent.keyDown(grid, { key: 'ArrowRight' })  // focus index 0
    Object.defineProperty(grid, 'scrollTop', { configurable: true, value: 34800 })
    fireEvent.scroll(grid)
    // idx 0 is far above the window but the focus branch keeps it mounted.
    expect(container.querySelector('.cell.focused')?.getAttribute('data-idx')).toBe('0')
  })

  it('prefetches the next page when scrolled near the bottom', () => {
    const fetchNextPage = vi.fn()
    const { getByRole } = renderGrid(30, { hasNextPage: true, fetchNextPage })
    const grid = getByRole('grid')
    expect(fetchNextPage).not.toHaveBeenCalled()  // not at the bottom yet
    Object.defineProperty(grid, 'scrollTop', { configurable: true, value: 3000 })
    fireEvent.scroll(grid)
    expect(fetchNextPage).toHaveBeenCalled()
  })

  it('moves roving focus by a full row on ArrowDown (cols-aware)', () => {
    const { container, getByRole } = renderGrid(30)
    const grid = getByRole('grid')
    fireEvent.keyDown(grid, { key: 'ArrowDown' })  // null → focus index 0
    expect(container.querySelector('.cell.focused')?.getAttribute('data-idx')).toBe('0')
    fireEvent.keyDown(grid, { key: 'ArrowDown' })  // 0 → 0 + cols (3)
    expect(container.querySelector('.cell.focused')?.getAttribute('data-idx')).toBe('3')
    fireEvent.keyDown(grid, { key: 'ArrowRight' }) // 3 → 4
    expect(container.querySelector('.cell.focused')?.getAttribute('data-idx')).toBe('4')
  })

  it('activates the focused cell on Enter', () => {
    const onActivate = vi.fn()
    const { getByRole } = renderGrid(30, { onActivate })
    const grid = getByRole('grid')
    fireEvent.keyDown(grid, { key: 'ArrowRight' }) // focus 0
    fireEvent.keyDown(grid, { key: 'Enter' })
    expect(onActivate).toHaveBeenCalledWith(0)
  })

  it('ignores keys while disabled (overlay owns them)', () => {
    const onActivate = vi.fn()
    const { container, getByRole } = renderGrid(30, { enabled: false, onActivate })
    fireEvent.keyDown(getByRole('grid'), { key: 'ArrowDown' })
    expect(container.querySelector('.cell.focused')).toBeNull()
  })

  it('shows empty and loading states', () => {
    const { getByText, rerender } = render(
      <WindowedPileGrid<Row>
        items={[]} getKey={(r) => r.id} metaHeight={44}
        hasNextPage={false} isFetchingNextPage={false} fetchNextPage={() => {}}
        onActivate={() => {}} loading emptyLabel="None" loadingLabel="Wait…"
        renderCell={() => null}
      />,
    )
    expect(getByText('Wait…')).toBeInTheDocument()
    rerender(
      <WindowedPileGrid<Row>
        items={[]} getKey={(r) => r.id} metaHeight={44}
        hasNextPage={false} isFetchingNextPage={false} fetchNextPage={() => {}}
        onActivate={() => {}} loading={false} emptyLabel="None" loadingLabel="Wait…"
        renderCell={() => null}
      />,
    )
    expect(getByText('None')).toBeInTheDocument()
  })
})
