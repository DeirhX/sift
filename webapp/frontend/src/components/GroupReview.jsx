import { useState, useEffect } from 'react'
import { thumbUrl } from '../api.js'
import Lightbox from './Lightbox.jsx'

// Review a duplicate group: a filmstrip of members up top, a large preview
// of the selected one below. Click any strip thumb to swap the preview;
// click the preview to open a full-size viewer that still navigates members.
export default function GroupReview({ group, onClose, onDecision, onDecisionsBulk, personName }) {
  const items = group.items
  const best = items[0]            // server returns members best-first
  const [sel, setSel] = useState(0)
  const [full, setFull] = useState(false)

  // Keep selection valid if the group's contents change underneath us.
  useEffect(() => { if (sel >= items.length) setSel(0) }, [items.length, sel])

  // Keyboard: arrows switch members, k/d decide, Esc closes — but only when
  // the full-size viewer isn't open (it handles its own keys).
  useEffect(() => {
    if (full) return
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight') setSel((s) => Math.min(s + 1, items.length - 1))
      else if (e.key === 'ArrowLeft') setSel((s) => Math.max(s - 1, 0))
      else if (e.key === 'k') onDecision(items[sel], 'keep')
      else if (e.key === 'd') onDecision(items[sel], 'del')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [full, items, sel, onClose, onDecision])

  const cur = items[sel]
  const fmt = (v) => (v == null ? '–' : v.toFixed(2))
  const aes = cur.para_aesthetic ?? cur.clip_iqa

  const keepBestDeleteRest = () => {
    onDecisionsBulk(items.map((it, i) => ({
      id: it.id, hash: it.hash, decision: i === 0 ? 'keep' : 'del',
    })))
  }
  const clearGroup = () => {
    onDecisionsBulk(items.map((it) => ({ id: it.id, hash: it.hash, decision: null })))
  }

  // Adapt Lightbox's setIndex (number to navigate, null to close).
  const lbSetIndex = (v) => {
    const n = typeof v === 'function' ? v(sel) : v
    if (n == null) setFull(false)
    else setSel(n)
  }

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="review-panel" onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <div>
            <b>Duplicate group #{group.dup_group}</b>
            <span className="review-sub"> · {items.length} photos · viewing {sel + 1}/{items.length}</span>
          </div>
          <div className="review-actions">
            <button className="btn primary" onClick={keepBestDeleteRest}>Keep best · delete rest</button>
            <button className="btn" onClick={clearGroup}>Clear</button>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>

        {/* Filmstrip of members */}
        <div className="review-strip">
          {items.map((it, i) => (
            <button
              key={it.id}
              className={'strip-thumb'
                + (i === sel ? ' active' : '')
                + (it.decision === 'del' ? ' is-del' : '')
                + (it.decision === 'keep' ? ' is-keep' : '')}
              onClick={() => setSel(i)}
              title={it.filename}
            >
              <img src={thumbUrl(it.id)} alt={it.filename} loading="lazy" />
              {it.id === best.id && <span className="strip-best">★</span>}
              {it.decision && <span className={'strip-flag ' + it.decision} />}
            </button>
          ))}
        </div>

        {/* Large preview of the selected member */}
        <div className="review-hero" onClick={() => setFull(true)} title="Click for full size">
          <img src={thumbUrl(cur.id)} alt={cur.filename} />
          {cur.id === best.id && <span className="badge-best">★ best</span>}
          {cur.decision && (
            <span className={'badge-decision ' + cur.decision}>
              {cur.decision === 'keep' ? 'KEEP' : 'DEL'}
            </span>
          )}
          <span className="hero-hint">Click for full size</span>
        </div>

        {/* Selected member info + decide */}
        <div className="review-herobar">
          <div className="herobar-info">
            <span className="herobar-name">{cur.filename}</span>
            <span className="herobar-scores">
              Q <b>{fmt(cur.combined)}</b> · Sh <b>{fmt(cur.sharpness)}</b> · Ae <b>{fmt(aes)}</b>
            </span>
          </div>
          <div className="herobar-btns">
            <button
              className={'keep' + (cur.decision === 'keep' ? ' active' : '')}
              onClick={() => onDecision(cur, 'keep')}
            >Keep</button>
            <button
              className={'del' + (cur.decision === 'del' ? ' active' : '')}
              onClick={() => onDecision(cur, 'del')}
            >Delete</button>
          </div>
        </div>
      </div>

      {full && (
        <Lightbox
          items={items}
          index={sel}
          setIndex={lbSetIndex}
          onDecision={onDecision}
          showStrip
        />
      )}
    </div>
  )
}
