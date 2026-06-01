import { useRef, useEffect } from 'react'
import ScenePile from './ScenePile.jsx'

// Overview of rough scenes as stacked photo piles. Clicking a pile asks the app
// to open its scene panel (`onOpen(scene_group)`); the panel itself lives at the
// app root so it can be URL-driven / Back-navigable.
export default function SceneView({ query, onOpen }) {
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

  if (query.isLoading) return <div className="spinner">Loading scenes…</div>
  if (scenes.length === 0) return <div className="empty">No scenes found.</div>

  return (
    <div className="grid-scroll">
      <div className="pile-grid scene-grid">
        {scenes.map((s) => (
          <ScenePile key={s.scene_group} scene={s} onOpen={() => onOpen(s.scene_group)} />
        ))}
      </div>
      <div ref={sentinelRef} />
      {isFetchingNextPage && <div className="spinner">Loading more…</div>}
    </div>
  )
}
