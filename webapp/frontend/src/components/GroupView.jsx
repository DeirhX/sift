import { useState, useRef, useEffect, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { autocullGroups } from '../api.js'
import GroupPile from './GroupPile.jsx'

// Overview of duplicate groups as stacked photo piles. Arrow keys move a
// keyboard focus across piles; Enter / click asks the app to open the review
// overlay (`onOpen(dup_group)`). The overlay itself lives at the app root so
// it is URL-driven / Back-navigable. `reviewOpen` lets the app pause grid keys
// while that overlay is up (the overlay handles its own keyboard).
export default function GroupView({ query, onOpen, reviewOpen = false }) {
  const [culling, setCulling] = useState(false)
  const [focusIdx, setFocusIdx] = useState(null)   // keyboard-focused pile
  const sentinelRef = useRef(null)
  const scrollRef = useRef(null)
  const pileGridRef = useRef(null)
  const qc = useQueryClient()

  const doAutocull = async () => {
    if (!window.confirm(
      'Across ALL duplicate groups, mark the best photo "keep" and the rest ' +
      '"delete"? This overwrites existing marks inside groups (files are not ' +
      'moved until you Apply).')) return
    setCulling(true)
    try {
      await autocullGroups()
      qc.invalidateQueries({ queryKey: ['groups'] })
      qc.invalidateQueries({ queryKey: ['images'] })
      qc.invalidateQueries({ queryKey: ['applyStatus'] })
    } finally {
      setCulling(false)
    }
  }

  const groups = query.data?.pages.flatMap((p) => p.groups) ?? []

  // Infinite scroll via an IntersectionObserver sentinel at the list end.
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage()
      }
    }, { rootMargin: '600px' })
    io.observe(el)
    return () => io.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  // Number of piles per row, read from the rendered grid (the CSS grid is
  // responsive, so we can't assume a fixed column count).
  const colsOf = useCallback(() => {
    const grid = pileGridRef.current
    if (!grid || grid.children.length === 0) return 1
    const top0 = grid.children[0].offsetTop
    let c = 0
    for (const ch of grid.children) {
      if (ch.offsetTop === top0) c++
      else break
    }
    return Math.max(1, c)
  }, [])

  const openReview = useCallback((idx) => {
    setFocusIdx(idx)
    const g = groups[idx]
    if (g) onOpen(g.dup_group)
  }, [groups, onOpen])

  // Focus the grid once it first has content, so arrow keys work immediately
  // after switching to the Groups view (no extra Tab needed).
  const didFocus = useRef(false)
  useEffect(() => {
    if (!didFocus.current && groups.length > 0 && scrollRef.current) {
      didFocus.current = true
      scrollRef.current.focus()
    }
  }, [groups.length])

  // Keep the focused pile in view.
  useEffect(() => {
    if (focusIdx == null) return
    pileGridRef.current?.children[focusIdx]?.scrollIntoView({ block: 'nearest' })
  }, [focusIdx])

  // Drop focus if the list shrinks under it.
  useEffect(() => {
    if (focusIdx != null && focusIdx > groups.length - 1) setFocusIdx(null)
  }, [groups.length, focusIdx])

  const onKeyDown = useCallback((e) => {
    if (reviewOpen) return   // the review modal handles its own keys
    if (/^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(e.target.tagName)) return
    const last = groups.length - 1
    if (last < 0) return
    const cols = colsOf()
    const cur = focusIdx == null ? 0 : focusIdx
    const move = (n) => {
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
        if (focusIdx != null) { e.preventDefault(); openReview(focusIdx) }
        return
      default: return
    }
  }, [reviewOpen, groups.length, focusIdx, colsOf, hasNextPage, isFetchingNextPage, fetchNextPage, openReview])

  if (query.isLoading) return <div className="spinner">Loading groups…</div>
  if (groups.length === 0) return <div className="empty">No duplicate groups found.</div>

  return (
    <div
      className="grid-scroll"
      ref={scrollRef}
      tabIndex={0}
      role="grid"
      aria-label="Duplicate groups — arrow keys to move, Enter to open, Esc to go back"
      onKeyDown={onKeyDown}
    >
      <div className="group-actionbar">
        <button className="btn primary" disabled={culling} onClick={doAutocull}>
          {culling ? 'Culling…' : 'Auto-cull all groups · keep best, delete rest'}
        </button>
        <span className="group-hint">Marks only — review or undo before applying.</span>
      </div>
      <div className="pile-grid" ref={pileGridRef}>
        {groups.map((g, i) => (
          <GroupPile
            key={g.dup_group}
            group={g}
            focused={i === focusIdx}
            onOpen={() => openReview(i)}
          />
        ))}
      </div>
      <div ref={sentinelRef} />
      {isFetchingNextPage && <div className="spinner">Loading more…</div>}
    </div>
  )
}
