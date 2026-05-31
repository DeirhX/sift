import { useState, useEffect } from 'react'
import { thumbUrl } from '../api.js'
import { groupByDup, fmtTimeRange } from '../format.js'
import GroupPile from './GroupPile.jsx'
import GroupReview from './GroupReview.jsx'
import Lightbox from './Lightbox.jsx'
import DecisionBadge from './DecisionBadge.jsx'

// Drill-down for a single scene: its near-duplicate sub-piles up top (each one
// opens the existing GroupReview), and the loose members (no near-dup twin)
// below as a thumb grid that opens the full-size Lightbox over the whole scene.
export default function ScenePanel({ scene, onClose, onDecision, onDecisionsBulk, personName }) {
  const { sets, loose } = groupByDup(scene.items)
  const [openDup, setOpenDup] = useState(null)   // dup_group id under review
  const [lightIdx, setLightIdx] = useState(null)  // index into scene.items

  // Esc closes the panel, but only when no nested overlay owns the keyboard.
  useEffect(() => {
    if (openDup != null || lightIdx != null) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openDup, lightIdx, onClose])

  const openSet = sets.find((s) => s.dup_group === openDup)
  const when = fmtTimeRange(scene.time_start, scene.time_end)

  // Lightbox navigates the full scene in display order; map clicks back to it.
  const lbSetIndex = (v) => {
    const n = typeof v === 'function' ? v(lightIdx) : v
    setLightIdx(n)
  }
  const idxOf = (it) => scene.items.findIndex((x) => x.id === it.id)

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="review-panel scene-panel" onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <div>
            <b>Scene #{scene.scene_group}</b>
            <span className="review-sub">
              {' '}· {scene.items.length} photos
              {sets.length > 0 && ` · ${sets.length} near-dup set${sets.length > 1 ? 's' : ''}`}
            </span>
            {when && <span className="review-filternote"> · {when}</span>}
          </div>
          <div className="review-actions">
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>

        {sets.length > 0 && (
          <>
            <div className="scene-section-label">Near-duplicate sets</div>
            <div className="pile-grid">
              {sets.map((s) => (
                <GroupPile
                  key={s.dup_group}
                  group={s}
                  onOpen={() => setOpenDup(s.dup_group)}
                />
              ))}
            </div>
          </>
        )}

        {loose.length > 0 && (
          <>
            <div className="scene-section-label">
              {sets.length > 0 ? 'Other photos in this scene' : 'Photos'}
            </div>
            <div className="scene-loose-grid">
              {loose.map((it) => (
                <button
                  key={it.id}
                  className={'scene-loose'
                    + (it.decision === 'del' ? ' is-del' : '')
                    + (it.decision === 'keep' ? ' is-keep' : '')}
                  onClick={() => setLightIdx(idxOf(it))}
                  title={it.filename}
                >
                  <img src={thumbUrl(it.id)} alt={it.filename} loading="lazy" />
                  <DecisionBadge decision={it.decision} />
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {openSet && (
        <GroupReview
          group={openSet}
          onClose={() => setOpenDup(null)}
          onDecision={onDecision}
          onDecisionsBulk={onDecisionsBulk}
          personName={personName}
        />
      )}

      {lightIdx != null && scene.items[lightIdx] && (
        <Lightbox
          items={scene.items}
          index={lightIdx}
          setIndex={lbSetIndex}
          onDecision={onDecision}
          showStrip
        />
      )}
    </div>
  )
}
