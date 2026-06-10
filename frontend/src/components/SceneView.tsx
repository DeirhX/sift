import { useCallback, useState, useMemo } from 'react'
import ScenePile from './ScenePile'
import WindowedPileGrid from './WindowedPileGrid'
import type { ScenesResponse } from '../api/types'

// Reserved height below each square stack: scene meta row + one clamped keyword
// row. Keeps pile cells uniform so the grid can window by row.
const SCENE_META_H = 80

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
  activeTags?: string[]
  onToggleTag?: (tag: string) => void
  onMerge?: (sceneGroups: number[]) => void | Promise<void>
  onUnmerge?: (sceneGroup: number) => void | Promise<void>
  // Trash filter active → piles count/show trashed members (passed to ScenePile).
  showTrashed?: boolean
}

// Overview of rough scenes as stacked photo piles, rendered through the
// windowed pile grid so only on-screen piles mount no matter how many scenes
// exist. Arrow keys move a keyboard focus; Enter / click opens the scene panel
// (`onOpen(scene_group)`), which lives at the app root so it can be URL-driven /
// Back-navigable. `reviewOpen` pauses grid keys while that panel is up.
export default function SceneView(
  { query, onOpen, reviewOpen = false, activeTags = [], onToggleTag,
    onMerge, onUnmerge, showTrashed = false }: SceneViewProps,
) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)

  const scenes = query.data?.pages.flatMap((p) => p.scenes) ?? []

  const toggleSelect = useCallback((sg: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(sg) ? next.delete(sg) : next.add(sg)
      return next
    })
  }, [])

  const selCount = selected.size
  // Unmerge is only meaningful for a single, manually-merged scene.
  const soleManual = useMemo(() => {
    if (selCount !== 1) return null
    const sg = [...selected][0]
    return scenes.find((s) => s.scene_group === sg && s.manual)?.scene_group ?? null
  }, [selCount, selected, scenes])

  const runMerge = useCallback(async () => {
    if (selCount < 2 || !onMerge) return
    setBusy(true)
    try { await onMerge([...selected]); setSelected(new Set()) }
    finally { setBusy(false) }
  }, [selCount, selected, onMerge])

  const runUnmerge = useCallback(async () => {
    if (soleManual == null || !onUnmerge) return
    setBusy(true)
    try { await onUnmerge(soleManual); setSelected(new Set()) }
    finally { setBusy(false) }
  }, [soleManual, onUnmerge])

  const activate = useCallback((idx: number) => {
    const s = scenes[idx]
    if (s) onOpen(s.scene_group)
  }, [scenes, onOpen])

  const header = selCount > 0 ? (
    <div className="scene-selbar" role="toolbar" aria-label="Scene merge actions">
      <span className="scene-selbar-count">{selCount} scene{selCount > 1 ? 's' : ''} selected</span>
      <span className="spacer" />
      <button
        type="button"
        className="btn primary"
        disabled={selCount < 2 || busy}
        onClick={runMerge}
        title="Pin the selected scenes into one (survives the slider and re-analysis)"
      >
        Merge {selCount} into one scene
      </button>
      {soleManual != null && (
        <button type="button" className="btn" disabled={busy} onClick={runUnmerge}
                title="Split this manually-merged scene back apart">
          Unmerge
        </button>
      )}
      <button type="button" className="btn ghost" disabled={busy}
              onClick={() => setSelected(new Set())}>
        Clear
      </button>
    </div>
  ) : null

  return (
    <WindowedPileGrid
      items={scenes}
      getKey={(s) => s.scene_group}
      metaHeight={SCENE_META_H}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      fetchNextPage={query.fetchNextPage}
      onActivate={activate}
      enabled={!reviewOpen}
      header={header}
      ariaLabel="Scenes — arrow keys to move, Enter to open, Esc to go back"
      emptyLabel="No scenes found."
      loading={query.isLoading}
      loadingLabel="Loading scenes…"
      renderCell={(s, i, focused) => (
        <ScenePile
          scene={s}
          focused={focused}
          onOpen={() => activate(i)}
          activeTags={activeTags}
          onToggleTag={onToggleTag}
          selected={selected.has(s.scene_group)}
          onToggleSelect={() => toggleSelect(s.scene_group)}
          showTrashed={showTrashed}
        />
      )}
    />
  )
}
