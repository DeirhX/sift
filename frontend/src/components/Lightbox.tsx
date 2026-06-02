import { useEffect, useCallback, useState, useRef } from 'react'
import type { MouseEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fullUrl, thumbUrl, fetchLocations, revealPath, fetchMeta } from '../api'
import { fmt, aestheticScore, fmtTime, qualityColor } from '../format'
import type { ImageItem, LocationsResponse } from '../api/types'
import type { DecisionFn, SetLightboxIndex } from '../types'

// Normalise a path for prefix comparison against the configured roots: lower
// case, forward slashes, no trailing slash (mirrors the server's normcase).
const normPath = (s: string): string => s.toLowerCase().replace(/\\/g, '/').replace(/\/+$/, '')
const withinRoots = (target: string, roots: string[]): boolean => {
  const t = normPath(target)
  return roots.some((r) => { const rr = normPath(r); return t === rr || t.startsWith(rr + '/') })
}

interface Crumb {
  label: string
  target: string
}

// Render a filesystem path as breadcrumb segments. Segments at or below a
// configured photo root open in the OS file manager (a directory opens, the
// filename reveals the file selected); segments above the root are inert (the
// server guardrail would reject them anyway), so we don't offer dead clicks.
function PathLink({ path, roots = [] }: { path: string; roots?: string[] }) {
  const sep = path.includes('\\') ? '\\' : '/'
  const parts = path.split(/[\\/]/)
  const items: Crumb[] = []
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
  const restrict = roots && roots.length > 0
  const open = (target: string) => (e: MouseEvent) => {
    e.stopPropagation()
    revealPath(target).catch(() => {})
  }
  return (
    <span className="path-link">
      {items.map((it, i) => {
        const clickable = !restrict || withinRoots(it.target, roots)
        return (
          <span key={i}>
            {i > 0 && <span className="path-sep">{sep}</span>}
            {clickable ? (
              <span className="path-seg" title={`Open ${it.target}`} onClick={open(it.target)}>
                {it.label}
              </span>
            ) : (
              <span className="path-seg-static">{it.label}</span>
            )}
          </span>
        )
      })}
    </span>
  )
}

interface LightboxProps {
  items: ImageItem[]
  index: number
  setIndex: SetLightboxIndex
  onDecision: DecisionFn
  showStrip?: boolean
}

// Full-resolution overlay with keyboard nav (←/→/Esc) and keep/del.
// When `showStrip` is set, a small thumbnail strip lets you jump between
// the supplied items (used for navigating duplicate-group members).
export default function Lightbox({ items, index, setIndex, onDecision, showStrip = false }: LightboxProps) {
  const item = items[index]
  // Read the already-cached meta to bound which path segments are clickable.
  const { data: meta } = useQuery({ queryKey: ['meta'], queryFn: fetchMeta, staleTime: Infinity })
  const roots = meta?.photo_roots ?? []

  const go = useCallback((delta: number) => {
    setIndex((i) => {
      const n = i + delta
      if (n < 0 || n >= items.length) return i
      return n
    })
  }, [items.length, setIndex])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIndex(null)
      else if (e.key === 'ArrowLeft') go(-1)
      else if (e.key === 'ArrowRight') go(1)
      else if (e.key === 'k' || e.key === '+' || e.key === '=') onDecision(item, 'keep')
      else if (e.key === 'd' || e.key === '-') onDecision(item, 'del')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, item, onDecision, setIndex])

  // Keep the active strip thumb in view as ←/→ walk past the rendered edge.
  const stripRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!showStrip) return
    stripRef.current?.querySelector('.lb-strip-thumb.active')
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' })
  }, [index, showStrip])

  // Exact-duplicate locations (same content hash) for the current image.
  const [locs, setLocs] = useState<LocationsResponse | null>(null)
  useEffect(() => {
    if (!item) return
    let alive = true
    setLocs(null)
    fetchLocations(item.id).then((d) => { if (alive) setLocs(d) }).catch(() => {})
    return () => { alive = false }
  }, [item?.id])

  // Rich tooltip for strip thumbnails: name, key scores, then caption.
  const describe = (it: ImageItem): string => {
    const parts = [it.filename,
      `Q ${fmt(it.combined)} · Sh ${fmt(it.sharpness)} · Ae ${fmt(aestheticScore(it))}`]
    if (it.portrait != null) parts.push(`Portrait ${fmt(it.portrait)}`)
    if (it.caption) parts.push(it.caption)
    return parts.join('\n')
  }

  if (!item) return null
  const aes = aestheticScore(item)

  // Per-axis stats shown as indicative bars. Each entry is only rendered when
  // the value is present, so libraries scored without PARA/faces degrade
  // gracefully (e.g. CLIP-IQA-only runs show Quality/Sharpness/Aesthetic).
  const stats: [string, number | null | undefined, string][] = [
    ['Quality', item.combined, 'Composite score (0.4·sharpness + 0.6·aesthetic)'],
    ['Sharpness', item.sharpness, 'Laplacian-variance sharpness, normalised'],
    ['Aesthetic', aes, 'PARA aesthetic (or CLIP-IQA fallback)'],
    ['Quality (PARA)', item.para_quality, 'PARA technical quality'],
    ['Composition', item.para_composition, 'PARA composition'],
    ['Lighting', item.para_light, 'PARA lighting'],
    ['Color', item.para_color, 'PARA color'],
    ['Depth of field', item.para_dof, 'PARA depth-of-field'],
    ['Content', item.para_content, 'PARA subject/content'],
    ['Portrait', item.portrait, 'Face sharpness × expression of the largest face'],
  ]

  return (
    <div className="lightbox" onClick={() => setIndex(null)}>
      <div
        className={'lb-stage' + (item.decision ? ' dec-' + item.decision : '')}
        onClick={() => setIndex(null)}
      >
        <button className="lb-close" onClick={(e) => { e.stopPropagation(); setIndex(null) }}>×</button>
        {index > 0 && (
          <button className="lb-nav prev" onClick={(e) => { e.stopPropagation(); go(-1) }}>‹</button>
        )}
        {index < items.length - 1 && (
          <button className="lb-nav next" onClick={(e) => { e.stopPropagation(); go(1) }}>›</button>
        )}
        <img src={fullUrl(item.id, item.hash)} alt={item.filename} onClick={(e) => e.stopPropagation()} />
        {item.decision && (
          <span className={'lb-decision ' + item.decision}>
            {item.decision === 'del' ? '✕ Marked for deletion' : '✓ Marked to keep'}
          </span>
        )}

        {showStrip && items.length > 1 && (
          <div className="lb-strip" ref={stripRef} onClick={(e) => e.stopPropagation()}>
            {items.map((it, i) => (
              <button
                key={it.id}
                className={'lb-strip-thumb'
                  + (i === index ? ' active' : '')
                  + (it.decision === 'del' ? ' is-del' : '')
                  + (it.decision === 'keep' ? ' is-keep' : '')}
                onClick={() => setIndex(i)}
                title={describe(it)}
              >
                <img src={thumbUrl(it.id, it.hash)} alt={it.filename} loading="lazy" />
              </button>
            ))}
          </div>
        )}
      </div>

      <aside className="lb-panel" onClick={(e) => e.stopPropagation()}>
        <div className="lb-panel-head">
          <span className="q-pill big" style={{ background: qualityColor(item.combined) }} title="Composite quality">
            Q {fmt(item.combined)}
          </span>
          <b className="lb-name" title={item.filename}>{item.filename}</b>
        </div>

        <div className="lb-actions">
          <button
            className="btn lb-act"
            style={{ background: item.decision === 'keep' ? 'var(--keep)' : undefined, color: item.decision === 'keep' ? '#06231a' : undefined }}
            onClick={() => onDecision(item, 'keep')}
          >Keep (k / +)</button>
          <button
            className="btn lb-act"
            style={{ background: item.decision === 'del' ? 'var(--del)' : undefined, color: item.decision === 'del' ? '#2a0a06' : undefined }}
            onClick={() => onDecision(item, 'del')}
          >Delete (d / −)</button>
        </div>

        {item.caption && <div className="lb-caption">{item.caption}</div>}

        <div className="lb-stats">
          {stats.map(([label, value, hint]) => (
            <StatBar key={label} label={label} value={value} hint={hint} />
          ))}
        </div>

        <div className="lb-facts">
          {(item.imgw && item.imgh) && (
            <div className="lb-fact"><span>Dimensions</span><span>{item.imgw} × {item.imgh}</span></div>
          )}
          {item.scene_group != null && (
            <div className="lb-fact"><span>Scene</span><span>#{item.scene_group}</span></div>
          )}
          {item.dup_group != null && (
            <div className="lb-fact"><span>Near-dup set</span><span>#{item.dup_group}</span></div>
          )}
          {fmtTime(item.capture_time) && (
            <div className="lb-fact"><span>Captured</span><span>{fmtTime(item.capture_time)}</span></div>
          )}
          {item.tags.length > 0 && (
            <div className="lb-fact tags"><span>Tags</span><span>{item.tags.join(', ')}</span></div>
          )}
        </div>

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
                    {l.exists ? <PathLink path={l.path} roots={roots} /> : <span>{l.path}  (missing)</span>}
                  </div>
                ))}
              </>
            ) : (
              <div className="lb-loc">
                {locs.locations[0]?.exists
                  ? <PathLink path={locs.locations[0].path} roots={roots} />
                  : <span>{locs.locations[0]?.path}  (missing)</span>}
              </div>
            )}
          </div>
        )}
      </aside>
    </div>
  )
}

// One labelled stat with an indicative fill bar (red → green by value).
function StatBar(
  { label, value, hint }: { label: string; value: number | null | undefined; hint: string },
) {
  if (value == null) return null
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100)
  return (
    <div className="lb-stat" title={hint}>
      <span className="lb-stat-label">{label}</span>
      <span className="lb-stat-track">
        <span className="lb-stat-fill" style={{ width: pct + '%', background: qualityColor(value) }} />
      </span>
      <span className="lb-stat-val">{fmt(value)}</span>
    </div>
  )
}
