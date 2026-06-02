import { useRef, useEffect } from 'react'
import ScenePile from './ScenePile'
import { applyDecisionHide } from '../format'
import type { ScenesResponse } from '../api/types'

// Minimal slice of the useInfiniteQuery result this view consumes (decoupled
// from react-query's generics; the real result is structurally assignable).
interface SceneViewQuery {
  data?: { pages: ScenesResponse[] }
  isLoading: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => unknown
}

interface SceneViewProps {
  query: SceneViewQuery
  onOpen: (sceneGroup: number) => void
  hideDel?: boolean
  activeTags?: string[]
  onToggleTag?: (tag: string) => void
}

// Overview of rough scenes as stacked photo piles. Clicking a pile asks the app
// to open its scene panel (`onOpen(scene_group)`); the panel itself lives at the
// app root so it can be URL-driven / Back-navigable. `hideDel` drops del-marked
// members (and any scene they emptied) so the overview shrinks live as you cull.
export default function SceneView(
  { query, onOpen, hideDel = false, activeTags = [], onToggleTag }: SceneViewProps,
) {
  const sentinelRef = useRef<HTMLDivElement>(null)

  let scenes = query.data?.pages.flatMap((p) => p.scenes) ?? []
  if (hideDel) {
    scenes = scenes
      .map((s) => ({ ...s, items: applyDecisionHide(s.items, 'notdel') }))
      .filter((s) => s.items.length > 0)
  }

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
          <ScenePile
            key={s.scene_group}
            scene={s}
            onOpen={() => onOpen(s.scene_group)}
            activeTags={activeTags}
            onToggleTag={onToggleTag}
          />
        ))}
      </div>
      <div ref={sentinelRef} />
      {isFetchingNextPage && <div className="spinner">Loading more…</div>}
    </div>
  )
}
