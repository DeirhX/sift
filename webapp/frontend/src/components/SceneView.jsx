import { useState, useRef, useEffect, useCallback } from 'react'
import ScenePile from './ScenePile.jsx'
import ScenePanel from './ScenePanel.jsx'

// Overview of rough scenes as stacked photo piles. Clicking a pile opens the
// scene panel, which nests the scene's near-duplicate sets (each reusing the
// duplicate-group review) plus its loose members.
export default function SceneView({ query, onDecision, onDecisionsBulk, people }) {
  const [openId, setOpenId] = useState(null)
  const sentinelRef = useRef(null)

  const scenes = query.data?.pages.flatMap((p) => p.scenes) ?? []

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

  const openScene = scenes.find((s) => s.scene_group === openId) || null

  if (query.isLoading) return <div className="spinner">Loading scenes…</div>
  if (scenes.length === 0) return <div className="empty">No scenes found.</div>

  return (
    <div className="grid-scroll">
      <div className="pile-grid scene-grid">
        {scenes.map((s) => (
          <ScenePile key={s.scene_group} scene={s} onOpen={() => setOpenId(s.scene_group)} />
        ))}
      </div>
      <div ref={sentinelRef} />
      {isFetchingNextPage && <div className="spinner">Loading more…</div>}

      {openScene && (
        <ScenePanel
          scene={openScene}
          onClose={() => setOpenId(null)}
          onDecision={onDecision}
          onDecisionsBulk={onDecisionsBulk}
          personName={personName}
        />
      )}
    </div>
  )
}
