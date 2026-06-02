import { useRef, useState, useLayoutEffect, useEffect, useCallback, useMemo } from 'react'
import type { UIEvent, KeyboardEvent } from 'react'
import PhotoCard from './PhotoCard'
import type { ImageItem, ClusterFacet } from '../api/types'
import type { DecisionFn, PersonName } from '../types'

const MIN_COL = 240   // min card width before adding another column
const GAP = 14
const META_H = 128    // fixed height reserved for the card meta block
const OVERSCAN = 600  // px rendered above/below the viewport
// Clamp tile aspect so panoramas / towers don't wreck the column rhythm.
const MIN_RATIO = 0.56   // widest (landscape): thumb height ≥ 0.56 * width
const MAX_RATIO = 1.9    // tallest (portrait):  thumb height ≤ 1.9  * width

type Direction = 'up' | 'down' | 'left' | 'right'

interface Tile {
  item: ImageItem
  index: number
  left: number
  top: number
  thumbH: number
  height: number
}

interface PhotoGridProps {
  items: ImageItem[]
  hasNext: boolean
  isFetchingNext: boolean
  fetchNext: () => void
  onOpen: (index: number) => void
  onDecision: DecisionFn
  people: ClusterFacet[]
  onFaceChange?: () => void
}

// Aspect-preserving masonry: each tile keeps its photo's proportions and tiles
// flow into the shortest column. Layout positions are precomputed from image
// dimensions so we can window (virtualize) by absolute Y without a row grid.
export default function PhotoGrid(
  { items, hasNext, isFetchingNext, fetchNext, onOpen, onDecision, people, onFaceChange }: PhotoGridProps,
) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [viewport, setViewport] = useState(0)
  const [scrollTop, setScrollTop] = useState(0)
  // Index of the keyboard-focused tile (roving focus). null until the grid is
  // first navigated; arrows then drive it and scroll it into view.
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

  const inner = Math.max(width - 28, MIN_COL) // minus grid-scroll padding
  const cols = Math.max(1, Math.floor((inner + GAP) / (MIN_COL + GAP)))
  const colWidth = Math.floor((inner - GAP * (cols - 1)) / cols)

  // Precompute the masonry layout: shortest-column packing.
  const { layout, totalHeight } = useMemo(() => {
    const colH = new Array<number>(cols).fill(0)
    const out: Tile[] = new Array(items.length)
    for (let i = 0; i < items.length; i++) {
      const it = items[i]
      let thumbH = colWidth
      if (it.imgw && it.imgh) {
        const ratio = Math.min(MAX_RATIO, Math.max(MIN_RATIO, it.imgh / it.imgw))
        thumbH = Math.round(colWidth * ratio)
      }
      const cardH = thumbH + META_H
      // shortest column
      let c = 0
      for (let k = 1; k < cols; k++) if (colH[k] < colH[c]) c = k
      const top = colH[c]
      out[i] = {
        item: it,
        index: i,
        left: c * (colWidth + GAP),
        top,
        thumbH,
        height: cardH,
      }
      colH[c] = top + cardH + GAP
    }
    return { layout: out, totalHeight: Math.max(0, ...colH) }
  }, [items, cols, colWidth])

  const onScroll = useCallback((e: UIEvent<HTMLDivElement>) => setScrollTop(e.currentTarget.scrollTop), [])

  // Infinite scroll: fetch the next page as we approach the bottom.
  useEffect(() => {
    if (!hasNext || isFetchingNext) return
    if (scrollTop + viewport >= totalHeight - viewport) fetchNext()
  }, [scrollTop, viewport, totalHeight, hasNext, isFetchingNext, fetchNext])

  // Only render tiles intersecting the viewport (+ overscan). The focused tile
  // is always rendered too, so its ring shows even mid-scroll.
  const top = scrollTop - OVERSCAN
  const bot = scrollTop + viewport + OVERSCAN
  const visible = useMemo(
    () => layout.filter((l) => (l.top < bot && l.top + l.height > top) || l.index === focusIdx),
    [layout, top, bot, focusIdx],
  )

  // Spatial neighbour for arrow keys. Left/right prefer the same row, up/down
  // the same column (weighting the off-axis distance), so movement feels
  // natural in the ragged masonry instead of jumping by raw index.
  const neighbor = useCallback((from: number, dir: Direction): number => {
    const cur = layout[from]
    if (!cur) return from
    const cx = cur.left + colWidth / 2
    const cy = cur.top + cur.height / 2
    let best = from
    let bestScore = Infinity
    for (const l of layout) {
      if (l.index === from) continue
      const x = l.left + colWidth / 2
      const y = l.top + l.height / 2
      const dx = x - cx
      const dy = y - cy
      let ok = false
      let score = 0
      if (dir === 'right') { ok = dx > 1; score = dx + Math.abs(dy) * 3 }
      else if (dir === 'left') { ok = dx < -1; score = -dx + Math.abs(dy) * 3 }
      else if (dir === 'down') { ok = dy > 1; score = dy + Math.abs(dx) * 3 }
      else if (dir === 'up') { ok = dy < -1; score = -dy + Math.abs(dx) * 3 }
      if (ok && score < bestScore) { bestScore = score; best = l.index }
    }
    return best
  }, [layout, colWidth])

  // Keep the focused tile within the scroll viewport.
  useEffect(() => {
    const el = scrollRef.current
    if (!el || focusIdx == null || !layout[focusIdx]) return
    const { top: t, height: h } = layout[focusIdx]
    if (t < el.scrollTop) el.scrollTo({ top: t - GAP, behavior: 'smooth' })
    else if (t + h > el.scrollTop + el.clientHeight) {
      el.scrollTo({ top: t + h - el.clientHeight + GAP, behavior: 'smooth' })
    }
  }, [focusIdx, layout])

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    // Don't hijack typing in the search box, face-editor select, etc.
    if (/^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement).tagName)) return
    const last = items.length - 1
    if (last < 0) return
    const move = (next: number) => {
      e.preventDefault()
      const n = Math.max(0, Math.min(last, next))
      setFocusIdx(n)
      if (hasNext && !isFetchingNext && n >= last - cols * 2) fetchNext()
    }
    const cur = focusIdx == null ? 0 : focusIdx
    switch (e.key) {
      case 'ArrowRight': return move(focusIdx == null ? 0 : neighbor(cur, 'right'))
      case 'ArrowLeft':  return move(focusIdx == null ? 0 : neighbor(cur, 'left'))
      case 'ArrowDown':  return move(focusIdx == null ? 0 : neighbor(cur, 'down'))
      case 'ArrowUp':    return move(focusIdx == null ? 0 : neighbor(cur, 'up'))
      case 'Home':       return move(0)
      case 'End':        return move(last)
      case 'PageDown':   return move(cur + cols * 3)
      case 'PageUp':     return move(cur - cols * 3)
      case 'Enter':
        if (focusIdx != null) { e.preventDefault(); onOpen(focusIdx) }
        return
      // + (keep) / − (delete) mirror k/d; '=' is the unshifted main-row + key.
      case 'k': case 'K': case '+': case '=':
        if (focusIdx != null) { e.preventDefault(); onDecision(items[focusIdx], 'keep') }
        return
      case 'd': case 'D': case '-':
        if (focusIdx != null) { e.preventDefault(); onDecision(items[focusIdx], 'del') }
        return
      default: return
    }
  }, [items, focusIdx, neighbor, cols, hasNext, isFetchingNext, fetchNext, onOpen, onDecision])

  // Drop focus if the list shrinks under it (e.g. after a filter change).
  useEffect(() => {
    if (focusIdx != null && focusIdx > items.length - 1) setFocusIdx(null)
  }, [items.length, focusIdx])

  const personName = useCallback<PersonName>((cid) => {
    const c = people.find((p) => p.cluster_id === cid)
    return c?.name?.trim() ? c.name : null
  }, [people])

  return (
    <div
      className="grid-scroll"
      ref={scrollRef}
      onScroll={onScroll}
      onKeyDown={onKeyDown}
      tabIndex={0}
      role="grid"
      aria-label="Photo results — arrow keys to move, Enter to open, k/d to keep/delete"
    >
      <div style={{ height: totalHeight, position: 'relative' }}>
        {visible.map((l) => (
          <div
            key={l.item.id}
            className={'card-slot' + (l.index === focusIdx ? ' focused' : '')}
            style={{
              position: 'absolute',
              top: 0,
              left: l.left,
              transform: `translateY(${l.top}px)`,
              width: colWidth,
              height: l.height,
            }}
            onMouseDown={() => setFocusIdx(l.index)}
          >
            <PhotoCard
              item={l.item}
              colWidth={colWidth}
              thumbH={l.thumbH}
              onOpen={() => { setFocusIdx(l.index); onOpen(l.index) }}
              onDecision={onDecision}
              personName={personName}
              people={people}
              onFaceChange={onFaceChange}
            />
          </div>
        ))}
      </div>
      {isFetchingNext && <div className="spinner">Loading more…</div>}
    </div>
  )
}
