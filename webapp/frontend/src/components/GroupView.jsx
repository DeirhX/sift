import { useState, useRef, useEffect, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { autocullGroups } from '../api.js'
import GroupPile from './GroupPile.jsx'
import GroupReview from './GroupReview.jsx'

// Overview of duplicate groups as stacked photo piles. Clicking a pile
// opens a review panel to compare members and pick which to delete.
export default function GroupView({ query, onDecision, onDecisionsBulk, people }) {
  const [openId, setOpenId] = useState(null)
  const [culling, setCulling] = useState(false)
  const sentinelRef = useRef(null)
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

  const personName = useCallback((cid) => {
    const c = people.find((p) => p.cluster_id === cid)
    return c?.name?.trim() ? c.name : null
  }, [people])

  const openGroup = groups.find((g) => g.dup_group === openId) || null

  if (query.isLoading) return <div className="spinner">Loading groups…</div>
  if (groups.length === 0) return <div className="empty">No duplicate groups found.</div>

  return (
    <div className="grid-scroll">
      <div className="group-actionbar">
        <button className="btn primary" disabled={culling} onClick={doAutocull}>
          {culling ? 'Culling…' : 'Auto-cull all groups · keep best, delete rest'}
        </button>
        <span className="group-hint">Marks only — review or undo before applying.</span>
      </div>
      <div className="pile-grid">
        {groups.map((g) => (
          <GroupPile key={g.dup_group} group={g} onOpen={() => setOpenId(g.dup_group)} />
        ))}
      </div>
      <div ref={sentinelRef} />
      {isFetchingNextPage && <div className="spinner">Loading more…</div>}

      {openGroup && (
        <GroupReview
          group={openGroup}
          onClose={() => setOpenId(null)}
          onDecision={onDecision}
          onDecisionsBulk={onDecisionsBulk}
          personName={personName}
        />
      )}
    </div>
  )
}
