import { useEffect, useCallback, useState } from 'react'
import { fullUrl, thumbUrl, fetchLocations, revealPath } from '../api.js'

// Render a filesystem path as breadcrumb segments: clicking a directory opens
// it in the OS file manager; clicking the filename reveals the file selected.
function PathLink({ path }) {
  const sep = path.includes('\\') ? '\\' : '/'
  const parts = path.split(/[\\/]/)
  const items = []
  let acc = ''
  parts.forEach((part, i) => {
    if (i === 0) {
      // Unix root (''), or a Windows drive ('E:') — anchor with a trailing sep.
      acc = (part === '' ? '' : part) + sep
      items.push({ label: part === '' ? sep : part, target: acc })
    } else if (part !== '') {
      acc = (acc.endsWith(sep) ? acc : acc + sep) + part
      items.push({ label: part, target: acc })
    }
  })
  const open = (target) => (e) => {
    e.stopPropagation()
    revealPath(target).catch(() => {})
  }
  return (
    <span className="path-link">
      {items.map((it, i) => (
        <span key={i}>
          {i > 0 && <span className="path-sep">{sep}</span>}
          <span className="path-seg" title={`Open ${it.target}`} onClick={open(it.target)}>
            {it.label}
          </span>
        </span>
      ))}
    </span>
  )
}

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
  const aes = item.para_aesthetic ?? item.clip_iqa
  const fmt = (v) => (v == null ? '–' : v.toFixed(2))
  const qColor = (v) =>
    v == null ? 'var(--border)' : `hsl(${Math.round(Math.max(0, Math.min(1, v)) * 120)}, 58%, 42%)`

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
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span className="q-pill big" style={{ background: qColor(item.combined) }} title="Composite quality">
            Q {fmt(item.combined)}
          </span>
          <b>{item.filename}</b>
          <span className="lb-sub">
            Sh {fmt(item.sharpness)} · Ae {fmt(aes)}
            {item.portrait != null && ` · Portrait ${fmt(item.portrait)}`}
          </span>
          {item.dup_group != null && <span className="lb-sub">dup #{item.dup_group}</span>}
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
                  >
                    {l.id === item.id ? '▶ ' : '\u00a0\u00a0\u00a0'}
                    {l.exists ? <PathLink path={l.path} /> : <span>{l.path}  (missing)</span>}
                  </div>
                ))}
              </>
            ) : (
              <div className="lb-loc">
                {locs.locations[0]?.exists
                  ? <PathLink path={locs.locations[0].path} />
                  : <span>{locs.locations[0]?.path}  (missing)</span>}
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
