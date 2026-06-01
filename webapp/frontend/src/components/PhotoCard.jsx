import { useState } from 'react'
import { thumbUrl, assignFace, deleteFace } from '../api.js'
import { fmt, aestheticScore } from '../format.js'
import DecisionBadge from './DecisionBadge.jsx'
import DecideButtons from './DecideButtons.jsx'

// One photo tile with face overlays, scores, caption, tags and keep/delete
// actions. thumbH is chosen by the grid to preserve the photo's aspect ratio;
// when it matches the image's proportions the offsets below collapse to zero
// (no crop), and when the grid clamps an extreme ratio the cover-crop math
// keeps face boxes aligned.
//
// Clicking a face box opens an inline editor (overlaying the meta block) to
// reassign the face to another person, spin up a new person, or delete a
// false-positive box. These are pure DB edits and last until the next
// build_db ingest.
export default function PhotoCard({ item, colWidth, thumbH, onOpen, onDecision, personName, people = [], onFaceChange }) {
  const aes = aestheticScore(item)
  const qColor = (v) =>
    v == null ? 'var(--border)' : `hsl(${Math.round(Math.max(0, Math.min(1, v)) * 120)}, 58%, 42%)`
  const [editFace, setEditFace] = useState(null)   // index into item.faces
  const [busy, setBusy] = useState(false)

  // Face boxes are stored in original-image pixel coords; scale to the
  // displayed thumbnail (centered cover-crop for any leftover overflow).
  const iw = item.imgw || 1
  const ih = item.imgh || 1
  const scale = Math.max(colWidth / iw, thumbH / ih)
  const dispW = iw * scale
  const dispH = ih * scale
  const offX = (dispW - colWidth) / 2
  const offY = (dispH - thumbH) / 2

  const face = editFace != null ? item.faces?.[editFace] : null

  const runFace = async (fn) => {
    if (busy) return
    setBusy(true)
    try {
      await fn()
      setEditFace(null)
      onFaceChange?.()
    } catch (e) {
      console.error(e)
      setBusy(false)
    }
  }

  const onSelect = (e) => {
    const v = e.target.value
    if (v === '__new__') {
      const name = window.prompt('Name for the new person (optional):') || null
      runFace(() => assignFace(face.id, { new_person: true, name }))
    } else {
      runFace(() => assignFace(face.id, { cluster_id: Number(v) }))
    }
  }

  return (
    <div className="card">
      <div className="thumb-wrap" style={{ height: thumbH }} onClick={onOpen}>
        <img className="thumb" src={thumbUrl(item.id, item.hash)} loading="lazy" alt={item.filename} />
        {item.faces?.map((f, i) => {
          const [x1, y1, x2, y2] = f.bbox
          const left = x1 * scale - offX
          const top = y1 * scale - offY
          const w = (x2 - x1) * scale
          const h = (y2 - y1) * scale
          return (
            <div
              key={f.id ?? i}
              className={'face-box editable' + (editFace === i ? ' selected' : '')}
              title={(personName(f.cluster_id) || `Person ${f.cluster_id}`) + ' · click to edit'}
              style={{ left, top, width: w, height: h }}
              onClick={(e) => { e.stopPropagation(); setEditFace(editFace === i ? null : i) }}
            />
          )
        })}
        {item.dup_group != null && <span className="badge-dup">dup #{item.dup_group}</span>}
        <DecisionBadge decision={item.decision} />
      </div>

      <div className="meta">
        <div className="scores">
          <span className="q-pill" style={{ background: qColor(item.combined) }} title="Composite quality">
            Q {fmt(item.combined)}
          </span>
          <span className="score-sub">Sh <b>{fmt(item.sharpness)}</b></span>
          <span className="score-sub">Ae <b>{fmt(aes)}</b></span>
        </div>
        {item.caption && <div className="caption">{item.caption}</div>}
        {item.tags?.length > 0 && (
          <div className="tags">
            {item.tags.slice(0, 5).map((t) => <span key={t} className="tag">{t}</span>)}
          </div>
        )}
        <div className="actions">
          <DecideButtons item={item} onDecision={onDecision} />
        </div>
      </div>

      {face && (
        <div className="face-editor" onClick={(e) => e.stopPropagation()}>
          <div className="face-editor-row">
            <span className="face-editor-label">
              Face {fmt(face.prob)}
              {face.sharp != null && ` · sharp ${fmt(face.sharp)}`}
              {face.expr != null && ` · expr ${fmt(face.expr)}`}
            </span>
            <select value={face.cluster_id ?? ''} onChange={onSelect} disabled={busy}>
              {people.every((p) => p.cluster_id !== face.cluster_id) && (
                <option value={face.cluster_id ?? ''}>Person {face.cluster_id}</option>
              )}
              {people.map((p) => (
                <option key={p.cluster_id} value={p.cluster_id}>
                  {p.name?.trim() ? p.name : `Person ${p.cluster_id}`} ({p.count})
                </option>
              ))}
              <option value="__new__">＋ New person…</option>
            </select>
          </div>
          <div className="face-editor-row">
            <button className="face-del" disabled={busy}
              onClick={() => runFace(() => deleteFace(face.id))}>Delete box</button>
            <button className="face-close" disabled={busy}
              onClick={() => setEditFace(null)}>Close</button>
          </div>
        </div>
      )}
    </div>
  )
}
