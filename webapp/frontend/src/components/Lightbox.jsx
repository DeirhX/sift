import { useEffect, useCallback, useState } from 'react'
import { fullUrl, thumbUrl, fetchLocations } from '../api.js'
import { fmt, aestheticScore } from '../format.js'

// Full-resolution overlay with keyboard nav (←/→/Esc) and keep/del.
// When `showStrip` is set, a small thumbnail strip lets you jump between
// the supplied items (used for navigating duplicate-group members).
export default function Lightbox({ items, index, setIndex, onDecision, showStrip = false }) {
  const item = items[index]

  const go = useCallback((delta) => {
    setIndex((i) => {
      const n = i + delta
      if (n < 0 || n >= items.length) return i
      return n
    })
  }, [items.length, setIndex])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') setIndex(null)
      else if (e.key === 'ArrowLeft') go(-1)
      else if (e.key === 'ArrowRight') go(1)
      else if (e.key === 'k') onDecision(item, 'keep')
      else if (e.key === 'd') onDecision(item, 'del')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, item, onDecision, setIndex])

  // Exact-duplicate locations (same content hash) for the current image.
  const [locs, setLocs] = useState(null)
  useEffect(() => {
    if (!item) return
    let alive = true
    setLocs(null)
    fetchLocations(item.id).then((d) => { if (alive) setLocs(d) }).catch(() => {})
    return () => { alive = false }
  }, [item?.id])

  if (!item) return null
  const aes = aestheticScore(item)

  return (
    <div className="lightbox" onClick={() => setIndex(null)}>
      <button className="lb-close" onClick={() => setIndex(null)}>×</button>
      {index > 0 && (
        <button className="lb-nav prev" onClick={(e) => { e.stopPropagation(); go(-1) }}>‹</button>
      )}
      {index < items.length - 1 && (
        <button className="lb-nav next" onClick={(e) => { e.stopPropagation(); go(1) }}>›</button>
      )}
      <img src={fullUrl(item.id)} alt={item.filename} onClick={(e) => e.stopPropagation()} />
      <div className="lb-info" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          <b>{item.filename}</b>
          <span>Q {fmt(item.combined)} · Sh {fmt(item.sharpness)} · Ae {fmt(aes)}</span>
          {item.dup_group != null && <span>dup #{item.dup_group}</span>}
          <div style={{ flex: 1 }} />
          <button
            className={'btn ' + (item.decision === 'keep' ? '' : '')}
            style={{ background: item.decision === 'keep' ? 'var(--keep)' : undefined, color: item.decision === 'keep' ? '#06231a' : undefined }}
            onClick={() => onDecision(item, 'keep')}
          >Keep (k)</button>
          <button
            className="btn"
            style={{ background: item.decision === 'del' ? 'var(--del)' : undefined, color: item.decision === 'del' ? '#2a0a06' : undefined }}
            onClick={() => onDecision(item, 'del')}
          >Delete (d)</button>
        </div>
        {item.caption && <div style={{ marginTop: 6, color: 'var(--text-dim)' }}>{item.caption}</div>}
        {locs && (
          <div className="lb-locations">
            {locs.count > 1 ? (
              <>
                <div className="lb-loc-head">
                  Exact duplicate — {locs.count} locations (identical bytes, one shared verdict)
                </div>
                {locs.locations.map((l) => (
                  <div
                    key={l.id}
                    className={'lb-loc' + (l.id === item.id ? ' current' : '') + (l.exists ? '' : ' missing')}
                    title={l.exists ? l.path : l.path + '  (file missing)'}
                  >
                    {l.id === item.id ? '▶ ' : '   '}{l.path}{l.exists ? '' : '  (missing)'}
                  </div>
                ))}
              </>
            ) : (
              <div className="lb-loc" title={locs.locations[0]?.path}>
                {locs.locations[0]?.path}
              </div>
            )}
          </div>
        )}
      </div>

      {showStrip && items.length > 1 && (
        <div className="lb-strip" onClick={(e) => e.stopPropagation()}>
          {items.map((it, i) => (
            <button
              key={it.id}
              className={'lb-strip-thumb'
                + (i === index ? ' active' : '')
                + (it.decision === 'del' ? ' is-del' : '')
                + (it.decision === 'keep' ? ' is-keep' : '')}
              onClick={() => setIndex(i)}
              title={it.filename}
            >
              <img src={thumbUrl(it.id)} alt={it.filename} loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
