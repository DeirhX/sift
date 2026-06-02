import { fmtTimeRange } from '../format.js'
import GroupReview from './GroupReview.jsx'

// Opening a scene reuses the exact duplicate-group review (hero + filmstrip),
// so every scene presents an open image the moment it opens — whether or not it
// contains near-duplicate sets. The scene's near-dups are still surfaced: their
// strip thumbs are badged, and selecting one offers a scoped "delete its
// near-dups" action. The whole-scene "keep best · delete rest" is intentionally
// hidden, since a scene's members aren't all duplicates of each other.
export default function ScenePanel({
  scene, selId, zoom, onSelect, onZoom, onClose, onDecision, onDecisionsBulk,
}) {
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
    />
  )
}
