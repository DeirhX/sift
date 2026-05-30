import { useRef, useState, useLayoutEffect, useEffect, useCallback, useMemo } from 'react'
import PhotoCard from './PhotoCard.jsx'

const MIN_COL = 240   // min card width before adding another column
const GAP = 14
const META_H = 128    // fixed height reserved for the card meta block
const OVERSCAN = 600  // px rendered above/below the viewport
// Clamp tile aspect so panoramas / towers don't wreck the column rhythm.
const MIN_RATIO = 0.56   // widest (landscape): thumb height ≥ 0.56 * width
const MAX_RATIO = 1.9    // tallest (portrait):  thumb height ≤ 1.9  * width

// Aspect-preserving masonry: each tile keeps its photo's proportions and tiles
// flow into the shortest column. Layout positions are precomputed from image
// dimensions so we can window (virtualize) by absolute Y without a row grid.
export default function PhotoGrid({ items, hasNext, isFetchingNext, fetchNext, onOpen, onDecision, people }) {
  const scrollRef = useRef(null)
  const [width, setWidth] = useState(0)
  const [viewport, setViewport] = useState(0)
  const [scrollTop, setScrollTop] = useState(0)

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
    const colH = new Array(cols).fill(0)
    const out = new Array(items.length)
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

  const onScroll = useCallback((e) => setScrollTop(e.currentTarget.scrollTop), [])

  // Infinite scroll: fetch the next page as we approach the bottom.
  useEffect(() => {
    if (!hasNext || isFetchingNext) return
    if (scrollTop + viewport >= totalHeight - viewport) fetchNext()
  }, [scrollTop, viewport, totalHeight, hasNext, isFetchingNext, fetchNext])

  // Only render tiles intersecting the viewport (+ overscan).
  const top = scrollTop - OVERSCAN
  const bot = scrollTop + viewport + OVERSCAN
  const visible = useMemo(
    () => layout.filter((l) => l.top < bot && l.top + l.height > top),
    [layout, top, bot],
  )

  const personName = useCallback((cid) => {
    const c = people.find((p) => p.cluster_id === cid)
    return c?.name?.trim() ? c.name : null
  }, [people])

  return (
    <div className="grid-scroll" ref={scrollRef} onScroll={onScroll}>
      <div style={{ height: totalHeight, position: 'relative' }}>
        {visible.map((l) => (
          <div
            key={l.item.id}
            style={{
              position: 'absolute',
              top: 0,
              left: l.left,
              transform: `translateY(${l.top}px)`,
              width: colWidth,
              height: l.height,
            }}
          >
            <PhotoCard
              item={l.item}
              colWidth={colWidth}
              thumbH={l.thumbH}
              onOpen={() => onOpen(l.index)}
              onDecision={onDecision}
              personName={personName}
            />
          </div>
        ))}
      </div>
      {isFetchingNext && <div className="spinner">Loading more…</div>}
    </div>
  )
}
