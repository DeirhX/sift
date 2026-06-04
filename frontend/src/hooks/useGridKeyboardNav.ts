import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'

interface GridKeyboardNavOpts {
  // Number of items (piles) currently rendered in the grid.
  count: number
  // Open/activate the focused item (Enter / programmatic). Receives its index.
  onActivate: (idx: number) => void
  // Page-query controls so arrowing near the end pre-fetches the next page.
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => unknown
  // Pause key handling while an overlay/review owns the keyboard.
  enabled?: boolean
}

interface GridKeyboardNav {
  focusIdx: number | null
  setFocusIdx: (idx: number | null) => void
  scrollRef: React.RefObject<HTMLDivElement>
  pileGridRef: React.RefObject<HTMLDivElement>
  onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => void
}

// Arrow-key roving focus across a responsive CSS-grid of equal-width tiles
// (the duplicate-group and scene pile grids). Column count is read from the
// rendered DOM so it tracks the responsive grid; the focused tile is scrolled
// into view and pre-fetches more as it nears the end. Extracted from GroupView
// so SceneView gets identical behaviour instead of being mouse-only.
//
// PhotoGrid is intentionally NOT a consumer: its ragged masonry needs geometric
// (nearest-neighbour) movement, not a fixed column count.
export function useGridKeyboardNav(
  { count, onActivate, hasNextPage, isFetchingNextPage, fetchNextPage, enabled = true }: GridKeyboardNavOpts,
): GridKeyboardNav {
  const [focusIdx, setFocusIdx] = useState<number | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pileGridRef = useRef<HTMLDivElement>(null)

  // Columns per row, measured from the rendered grid (responsive, so we can't
  // assume a fixed count): count children sharing the first child's offsetTop.
  const colsOf = useCallback(() => {
    const grid = pileGridRef.current
    if (!grid || grid.children.length === 0) return 1
    const top0 = (grid.children[0] as HTMLElement).offsetTop
    let c = 0
    for (const ch of Array.from(grid.children) as HTMLElement[]) {
      if (ch.offsetTop === top0) c++
      else break
    }
    return Math.max(1, c)
  }, [])

  // Focus the scroll container once it first has content, so arrow keys work
  // immediately after switching to the view (no extra Tab needed).
  const didFocus = useRef(false)
  useEffect(() => {
    if (!didFocus.current && count > 0 && scrollRef.current) {
      didFocus.current = true
      scrollRef.current.focus()
    }
  }, [count])

  // Keep the focused tile in view.
  useEffect(() => {
    if (focusIdx == null) return
    pileGridRef.current?.children[focusIdx]?.scrollIntoView({ block: 'nearest' })
  }, [focusIdx])

  // Drop focus if the list shrinks under it.
  useEffect(() => {
    if (focusIdx != null && focusIdx > count - 1) setFocusIdx(null)
  }, [count, focusIdx])

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (!enabled) return   // an overlay/review handles its own keys
    if (/^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test((e.target as HTMLElement).tagName)) return
    const last = count - 1
    if (last < 0) return
    const cols = colsOf()
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
  }, [enabled, count, colsOf, focusIdx, hasNextPage, isFetchingNextPage, fetchNextPage, onActivate])

  return { focusIdx, setFocusIdx, scrollRef, pileGridRef, onKeyDown }
}
