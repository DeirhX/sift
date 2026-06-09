import { fmtTimeRange } from '../format'
import type { Scene } from '../api/types'
import type { DecisionFn, BulkDecisionFn } from '../types'
import GroupReview from './GroupReview'

interface ScenePanelProps {
  scene: Scene
  selId: number | null
  zoom: boolean
  onSelect: (id: number) => void
  onZoom: (open: boolean) => void
  onClose: () => void
  onDecision: DecisionFn
  onDecisionsBulk: BulkDecisionFn
  onApplyDeletes?: (ids: number[]) => Promise<void>
  defaultShowDeleted?: boolean
  defaultShowCulled?: boolean
}

// Opening a scene reuses the exact duplicate-group review (hero + filmstrip),
// so every scene presents an open image the moment it opens — whether or not it
// contains near-duplicate sets. The scene's near-dups are still surfaced: their
// strip thumbs are badged, and selecting one offers a scoped "delete its
// near-dups" action. The whole-scene "keep best · delete rest" is intentionally
// hidden, since a scene's members aren't all duplicates of each other.
export default function ScenePanel({
  scene, selId, zoom, onSelect, onZoom, onClose, onDecision, onDecisionsBulk,
  onApplyDeletes, defaultShowDeleted, defaultShowCulled,
}: ScenePanelProps) {
  const when = fmtTimeRange(scene.time_start, scene.time_end)
  return (
    <GroupReview
      group={scene}
      mode="scene"
      showGroupBulk={false}
      title={`Scene #${scene.scene_group}`}
      subExtra={when ? <span className="review-filternote"> · {when}</span> : null}
      selId={selId}
      zoom={zoom}
      onSelect={onSelect}
      onZoom={onZoom}
      onClose={onClose}
      onDecision={onDecision}
      onDecisionsBulk={onDecisionsBulk}
      onApplyDeletes={onApplyDeletes}
      defaultShowDeleted={defaultShowDeleted}
      defaultShowCulled={defaultShowCulled}
    />
  )
}
