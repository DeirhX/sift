import { useRef, useState, useLayoutEffect, useEffect, useCallback, useMemo } from 'react'
import type { UIEvent, KeyboardEvent, ReactNode } from 'react'

const COL_GAP = 18    // matches the old .pile-grid column gap
const ROW_GAP = 26    // matches the old .pile-grid row gap
const PAD = 28        // grid-scroll horizontal padding (14 each side)
const OVERSCAN = 600  // px rendered above/below the viewport

interface WindowedPileGridProps<T> {
  items: T[]
  getKey: (item: T, index: number) => React.Key
  // Render a single cell. `focused` drives the keyboard focus ring on the pile.
  renderCell: (item: T, index: number, focused: boolean) => ReactNode
  // Fixed non-thumbnail height reserved below each square stack. Row height is
  // `colWidth + metaHeight`, so cells stay uniform without measuring the DOM.
  metaHeight: number
  cellMinWidth?: number
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => unknown
  // Open/activate a cell by index (Enter, or click via renderCell).
  onActivate: (index: number) => void
  // Pause keyboard handling while an overlay/review owns the keys.
  enabled?: boolean
  // Non-scrolling content pinned above the scroll area (action bars, task panel).
  header?: ReactNode
  ariaLabel?: string
  emptyLabel?: string
  loading?: boolean
  loadingLabel?: string
}

// A windowed (virtualized) grid of equal-width cells, mirroring PhotoGrid's
// proven viewport-windowing but for uniform square piles: only the rows
// intersecting the viewport (+overscan) are mounted, so deep scrolling over
// thousands of scene/group piles can never balloon the DOM. Columns are derived
// from the measured container width; row height is computed (square stack +
// fixed meta) so positions are known without per-cell measurement. Roving
// arrow-key focus, scroll-into-view and near-end prefetch are built in.
export default function WindowedPileGrid<T>({
  items, getKey, renderCell, metaHeight, cellMinWidth = 220,
  hasNextPage, isFetchingNextPage, fetchNextPage, onActivate,
  enabled = true, header, ariaLabel, emptyLabel = 'Nothing here.',
  loading = false, loadingLabel = 'Loading…',
}: WindowedPileGridProps<T>) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [viewport, setViewport] = useState(0)
  const [scrollTop, setScrollTop] = useState(0)
  // Roving keyboard focus; null until the grid is first navigated.
  const [focusIdx, setFocusIdx] = useState<number | null>(null)

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width)
      setViewport(el.clientHeight)
    })
    ro.observe(el)
    setWidth(el.clientWidth)
    setViewport(el.clientHeight)
    return () => ro.disconnect()
  }, [])

  const inner = Math.max(width - PAD, cellMinWidth)
  const cols = Math.max(1, Math.floor((inner + COL_GAP) / (cellMinWidth + COL_GAP)))
  const colWidth = Math.floor((inner - COL_GAP * (cols - 1)) / cols)
  const rowH = colWidth + metaHeight
  const rowStride = rowH + ROW_GAP
  const rowCount = Math.ceil(items.length / cols)
  const totalHeight = rowCount > 0 ? rowCount * rowH + (rowCount - 1) * ROW_GAP : 0

  const onScroll = useCallback((e: UIEvent<HTMLDivElement>) => setScrollTop(e.currentTarget.scrollTop), [])

  // Mount only the rows intersecting the viewport (+overscan); always include
  // the focused cell so its ring shows even when scrolled away.
  const visible = useMemo(() => {
    if (items.length === 0) return []
    const firstRow = Math.max(0, Math.floor((scrollTop - OVERSCAN) / rowStride))
    const lastRow = Math.min(rowCount - 1, Math.floor((scrollTop + viewport + OVERSCAN) / rowStride))
    const out: { item: T; index: number; left: number; top: number }[] = []
    for (let r = firstRow; r <= lastRow; r++) {
      for (let c = 0; c < cols; c++) {
        const idx = r * cols + c
        if (idx >= items.length) break
        out.push({ item: items[idx], index: idx, left: c * (colWidth + COL_GAP), top: r * rowStride })
      }
    }
    if (focusIdx != null && focusIdx < items.length) {
      const fr = Math.floor(focusIdx / cols)
      if (fr < firstRow || fr > lastRow) {
        const fc = focusIdx % cols
        out.push({ item: items[focusIdx], index: focusIdx, left: fc * (colWidth + COL_GAP), top: fr * rowStride })
      }
    }
    return out
  }, [items, scrollTop, viewport, cols, colWidth, rowStride, rowCount, focusIdx])

  // Infinite scroll: fetch the next page as we approach the bottom. A scroll
  // threshold is used rather than a sentinel because the list end is unmounted.
  useEffect(() => {
    if (!hasNextPage || isFetchingNextPage || viewport === 0) return
    if (scrollTop + viewport >= totalHeight - viewport) fetchNextPage()
  }, [scrollTop, viewport, totalHeight, hasNextPage, isFetchingNextPage, fetchNextPage])

  // Focus the scroll container once it first has content, so arrow keys work
  // immediately after switching to the view (no extra Tab needed).
  const didFocus = useRef(false)
  useEffect(() => {
    if (!didFocus.current && items.length > 0 && scrollRef.current) {
      didFocus.current = true
      scrollRef.current.focus()
    }
  }, [items.length])

  // Drop focus if the list shrinks under it.
  useEffect(() => {
    if (focusIdx != null && focusIdx > items.length - 1) setFocusIdx(null)
  }, [items.length, focusIdx])

  // Keep the focused cell within the scroll viewport.
  useEffect(() => {
    const el = scrollRef.current
    if (!el || focusIdx == null || focusIdx >= items.length) return
    const top = Math.floor(focusIdx / cols) * rowStride
    const bottom = top + rowH
    if (top < el.scrollTop) el.scrollTo({ top: Math.max(0, top - ROW_GAP), behavior: 'smooth' })
    else if (bottom > el.scrollTop + el.clientHeight) {
      el.scrollTo({ top: bottom - el.clientHeight + ROW_GAP, behavior: 'smooth' })
    }
  }, [focusIdx, cols, rowStride, rowH, items.length])

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (!enabled) return
    if (/^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test((e.target as HTMLElement).tagName)) return
    const last = items.length - 1
    if (last < 0) return
    const cur = focusIdx == null ? 0 : focusIdx
    const move = (n: number) => {
      e.preventDefault()
      const ni = Math.max(0, Math.min(last, n))
      setFocusIdx(ni)
      if (hasNextPage && !isFetchingNextPage && ni >= last - cols * 2) fetchNextPage()
    }
    switch (e.key) {
      case 'ArrowRight': return move(focusIdx == null ? 0 : cur + 1)
      case 'ArrowLeft':  return move(focusIdx == null ? 0 : cur - 1)
      case 'ArrowDown':  return move(focusIdx == null ? 0 : cur + cols)
      case 'ArrowUp':    return move(focusIdx == null ? 0 : cur - cols)
      case 'Home':       return move(0)
      case 'End':        return move(last)
      case 'PageDown':   return move(cur + cols * 3)
      case 'PageUp':     return move(cur - cols * 3)
      case 'Enter':
        if (focusIdx != null) { e.preventDefault(); onActivate(focusIdx) }
        return
      default: return
    }
  }, [enabled, items.length, focusIdx, cols, hasNextPage, isFetchingNextPage, fetchNextPage, onActivate])

  return (
    <div className="windowed-view">
      {header && <div className="windowed-header">{header}</div>}
      <div
        className="grid-scroll"
        ref={scrollRef}
        onScroll={onScroll}
        onKeyDown={onKeyDown}
        tabIndex={0}
        role="grid"
        aria-label={ariaLabel}
      >
        {loading ? (
          <div className="spinner">{loadingLabel}</div>
        ) : items.length === 0 ? (
          <div className="empty">{emptyLabel}</div>
        ) : (
          <div style={{ position: 'relative', height: totalHeight }}>
            {visible.map(({ item, index, left, top }) => (
              <div
                key={getKey(item, index)}
                className="pile-slot"
                style={{
                  position: 'absolute',
                  top: 0,
                  left,
                  transform: `translateY(${top}px)`,
                  width: colWidth,
                  height: rowH,
                }}
                onMouseDown={() => setFocusIdx(index)}
              >
                {renderCell(item, index, index === focusIdx)}
              </div>
            ))}
          </div>
        )}
        {isFetchingNextPage && <div className="spinner">Loading more…</div>}
      </div>
    </div>
  )
}
