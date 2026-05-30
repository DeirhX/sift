import { useRef, useState, useLayoutEffect, useEffect, useCallback } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import PhotoCard from './PhotoCard.jsx'

const MIN_COL = 240   // min card width before adding another column
const GAP = 14
const META_H = 128    // fixed height reserved for the card meta block

export default function PhotoGrid({ items, hasNext, isFetchingNext, fetchNext, onOpen, onDecision, people }) {
  const scrollRef = useRef(null)
  const [width, setWidth] = useState(0)

  // Track container width to compute responsive column count.
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width)
    })
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const inner = Math.max(width - 28, MIN_COL) // minus grid-scroll padding
  const cols = Math.max(1, Math.floor((inner + GAP) / (MIN_COL + GAP)))
  const colWidth = Math.floor((inner - GAP * (cols - 1)) / cols)
  const rowHeight = colWidth + META_H + GAP
  const rowCount = Math.ceil(items.length / cols)

  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 4,
  })

  // Infinite scroll: load more when the last virtual row is rendered.
  const virtualRows = rowVirtualizer.getVirtualItems()
  useEffect(() => {
    const last = virtualRows[virtualRows.length - 1]
    if (!last) return
    if (last.index >= rowCount - 2 && hasNext && !isFetchingNext) {
      fetchNext()
    }
  }, [virtualRows, rowCount, hasNext, isFetchingNext, fetchNext])

  const personName = useCallback((cid) => {
    const c = people.find((p) => p.cluster_id === cid)
    return c?.name?.trim() ? c.name : null
  }, [people])

  return (
    <div className="grid-scroll" ref={scrollRef}>
      <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
        {virtualRows.map((vr) => {
          const start = vr.index * cols
          const rowItems = items.slice(start, start + cols)
          return rowItems.map((item, c) => (
            <div
              key={item.id}
              style={{
                position: 'absolute',
                top: 0,
                left: c * (colWidth + GAP),
                transform: `translateY(${vr.start}px)`,
                width: colWidth,
                height: rowHeight - GAP,
              }}
            >
              <PhotoCard
                item={item}
                colWidth={colWidth}
                thumbH={colWidth}
                onOpen={() => onOpen(start + c)}
                onDecision={onDecision}
                personName={personName}
              />
            </div>
          ))
        })}
      </div>
      {isFetchingNext && <div className="spinner">Loading more…</div>}
    </div>
  )
}
