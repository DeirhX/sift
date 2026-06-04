import { useRef, useCallback } from 'react'
import ScenePile from './ScenePile'
import { hideDelContainers } from '../format'
import { useInfiniteScroll } from '../hooks/useInfiniteScroll'
import { useGridKeyboardNav } from '../hooks/useGridKeyboardNav'
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
  reviewOpen?: boolean
  hideDel?: boolean
  activeTags?: string[]
  onToggleTag?: (tag: string) => void
}

// Overview of rough scenes as stacked photo piles. Arrow keys move a keyboard
// focus across piles; Enter / click asks the app to open the scene panel
// (`onOpen(scene_group)`), which lives at the app root so it can be URL-driven /
// Back-navigable. `reviewOpen` pauses grid keys while that panel is up.
// `hideDel` drops del-marked members (and any scene they emptied) so the
// overview shrinks live as you cull.
export default function SceneView(
  { query, onOpen, reviewOpen = false, hideDel = false, activeTags = [], onToggleTag }: SceneViewProps,
) {
  const sentinelRef = useRef<HTMLDivElement>(null)

  const rawScenes = query.data?.pages.flatMap((p) => p.scenes) ?? []
  const scenes = hideDelContainers(rawScenes, hideDel ? 'notdel' : '')

  useInfiniteScroll(sentinelRef, query)

  const activate = useCallback((idx: number) => {
    const s = scenes[idx]
    if (s) onOpen(s.scene_group)
  }, [scenes, onOpen])

  const { focusIdx, setFocusIdx, scrollRef, pileGridRef, onKeyDown } = useGridKeyboardNav({
    count: scenes.length,
    onActivate: activate,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
    enabled: !reviewOpen,
  })

  if (query.isLoading) return <div className="spinner">Loading scenes…</div>
  if (scenes.length === 0) return <div className="empty">No scenes found.</div>

  return (
    <div
      className="grid-scroll"
      ref={scrollRef}
      tabIndex={0}
      role="grid"
      aria-label="Scenes — arrow keys to move, Enter to open, Esc to go back"
      onKeyDown={onKeyDown}
    >
      <div className="pile-grid scene-grid" ref={pileGridRef}>
        {scenes.map((s, i) => (
          <ScenePile
            key={s.scene_group}
            scene={s}
            focused={i === focusIdx}
            onOpen={() => { setFocusIdx(i); activate(i) }}
            activeTags={activeTags}
            onToggleTag={onToggleTag}
          />
        ))}
      </div>
      <div ref={sentinelRef} />
      {query.isFetchingNextPage && <div className="spinner">Loading more…</div>}
    </div>
  )
}
