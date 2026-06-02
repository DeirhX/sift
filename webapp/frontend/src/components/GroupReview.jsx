import { useState, useEffect, useMemo } from 'react'
import { thumbUrl, fullUrl } from '../api.js'
import { fmt, aestheticScore, groupByDup, repFirst } from '../format.js'
import Lightbox from './Lightbox.jsx'
import DecisionBadge from './DecisionBadge.jsx'
import DecideButtons from './DecideButtons.jsx'

// Review a set of photos: a filmstrip of members up top, a large preview of the
// selected one below. Click any strip thumb to swap the preview; click the
// preview to open a full-size viewer that still navigates members.
//
// `mode` toggles between two callers that share this exact UI so the experience
// is uniform:
//   'group' — a near-duplicate group; offers "keep best · delete rest".
//   'scene' — a whole rough scene; the strip is GROUPED by default (each
//             near-duplicate set collapses to one stacked ×N tile, medoid-led,
//             so the scene reads as its distinct shots at a glance). Clicking a
//             tile expands that set inline as a bracketed cluster; an "Ungroup"
//             toggle flattens to every photo (with each set bound by a subtle
//             "group rail"). The group-wide "keep best · delete rest" is hidden
//             (a scene's members aren't all duplicates), but the selected
//             photo's own near-dup twins can still be deduped in one click.
// `title`/`subExtra` let the caller label the header; `showGroupBulk` gates the
// group-wide actions.
export default function GroupReview({
  group, onClose, onDecision, onDecisionsBulk, personName,
  mode = 'group', title = null, subExtra = null, showGroupBulk = true,
  selId = null, zoom = false, onSelect, onZoom,
}) {
  const items = group.items

  // Scene mode arranges the strip as a tree: sets first (each contiguous), then
  // loose photos. `view` is that flattened order so the hero, arrows and zoom
  // all navigate it consistently. Clusters are only drawn when the scene
  // actually branches (more than one child) — a lone set collapses to a plain
  // strip, the single-child rule that keeps trivial scenes simple. Each set is
  // reordered so its medoid leads, making the cluster's first thumb its hero.
  const { sets, loose } = useMemo(() => {
    if (mode !== 'scene') return { sets: [], loose: [] }
    const g = groupByDup(items)
    return { sets: g.sets.map((s) => ({ ...s, items: repFirst(s.items) })), loose: g.loose }
  }, [items, mode])
  const view = useMemo(
    () => (mode === 'scene' ? [...sets.flatMap((s) => s.items), ...loose] : repFirst(items)),
    [mode, sets, loose, items],
  )
  // A scene with near-dup sets opens grouped by default — each set collapses to
  // a single stacked representative tile you can click to expand inline — and an
  // "Ungroup" toggle flattens it to every photo (with group rails).
  const canGroup = mode === 'scene' && sets.length > 0
  // ★ marks each group's representative (the medoid hero), which is now the
  // first member of every reordered set / of `view` in group mode.
  const bestIds = useMemo(
    () => (mode === 'scene'
      ? new Set(sets.map((s) => s.items[0]?.id))
      : new Set(view.length ? [view[0].id] : [])),
    [mode, sets, view],
  )
  const idIndex = useMemo(() => new Map(view.map((it, i) => [it.id, i])), [view])

  // Selection + zoom are controlled by the app (so they live in the URL and
  // browser history). `selId` null → show the default hero (view[0], the
  // medoid). `full` mirrors the `zoom` prop. Helpers translate the existing
  // index-based call sites back to id-based callbacks.
  const sel = (() => {
    const i = selId != null ? idIndex.get(selId) : undefined
    return i == null ? 0 : i
  })()
  const full = !!zoom
  const selectIdx = (i) => {
    if (i == null || i < 0 || i >= view.length) return
    onSelect(view[i].id)
  }
  const [showList, setShowList] = useState(false)
  // Scene strip is grouped by default (near-dup sets collapsed to ×N tiles);
  // `expanded` tracks which collapsed sets are opened inline. For non-scene
  // callers canGroup is false, so this is inert.
  const [grouped, setGrouped] = useState(canGroup)
  const [expanded, setExpanded] = useState(() => new Set())
  const toggleExpand = (gid) => setExpanded((prev) => {
    const next = new Set(prev)
    if (next.has(gid)) next.delete(gid); else next.add(gid)
    return next
  })

  // Tooltip / list text for a member: name, key scores, then the caption.
  // \n works in title tooltips; the list renders the same parts as elements.
  const describe = (it) => {
    const parts = [it.filename,
      `Q ${fmt(it.combined)} · Sh ${fmt(it.sharpness)} · Ae ${fmt(aestheticScore(it))}`]
    if (it.portrait != null) parts.push(`Portrait ${fmt(it.portrait)}`)
    if (it.caption) parts.push(it.caption)
    if (it.matches === false) parts.push('(outside filter)')
    return parts.join('\n')
  }

  // How many members pass the active filters (server-computed). When some
  // don't, the filmstrip dims them so you still see the full duplicate set.
  const matchCount = items.filter((it) => it.matches !== false).length
  const someFiltered = matchCount < items.length

  // Keyboard: arrows switch members, k/d decide, Esc closes — but only when
  // the full-size viewer isn't open (it handles its own keys).
  useEffect(() => {
    if (full) return
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight') selectIdx(Math.min(sel + 1, view.length - 1))
      else if (e.key === 'ArrowLeft') selectIdx(Math.max(sel - 1, 0))
      else if (e.key === 'k') onDecision(view[sel], 'keep')
      else if (e.key === 'd') onDecision(view[sel], 'del')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [full, view, sel, onClose, onDecision])

  const cur = view[sel] ?? view[0]
  const aes = aestheticScore(cur)

  // Metric bars for whatever scores this image has (all on a 0–1 scale).
  const metrics = [
    ['Quality', cur.combined],
    ['Sharpness', cur.sharpness],
    ['Aesthetic', aes],
    ['Composition', cur.para_composition],
    ['Light', cur.para_light],
    ['Portrait', cur.portrait],
    ['Face sharp', cur.face_sharp],
    ['Expression', cur.face_expr],
  ].filter(([, v]) => v != null)
  // Red (low) → amber → green (high), so quality reads at a glance.
  const barColor = (v) => `hsl(${Math.round(Math.max(0, Math.min(1, v)) * 120)}, 65%, 45%)`

  const keepBestDeleteRest = () => {
    const keepId = view[0]?.id
    onDecisionsBulk(items.map((it) => ({
      id: it.id, hash: it.hash, decision: it.id === keepId ? 'keep' : 'del',
    })))
  }
  const clearGroup = () => {
    onDecisionsBulk(items.map((it) => ({ id: it.id, hash: it.hash, decision: null })))
  }

  // The selected photo's near-duplicate siblings (scene mode): keep the current
  // frame and drop just its twins, without touching the rest of the scene — the
  // dedupe workflow, scoped to one branch of the tree.
  const dupSiblings = cur.dup_group == null
    ? [] : items.filter((it) => it.dup_group === cur.dup_group)
  const keepThisDeleteDups = () => {
    onDecisionsBulk(dupSiblings.map((it) => ({
      id: it.id, hash: it.hash, decision: it.id === cur.id ? 'keep' : 'del',
    })))
  }

  // Adapt Lightbox's setIndex (number to navigate, null to close the zoom).
  const lbSetIndex = (v) => {
    const n = typeof v === 'function' ? v(sel) : v
    if (n == null) onZoom(false)
    else if (n !== sel) selectIdx(n)
  }

  const renderThumb = (it) => {
    const i = idIndex.get(it.id)
    return (
      <button
        key={it.id}
        className={'strip-thumb'
          + (i === sel ? ' active' : '')
          + (it.matches === false ? ' filtered' : '')
          + (it.decision === 'del' ? ' is-del' : '')
          + (it.decision === 'keep' ? ' is-keep' : '')}
        onClick={() => selectIdx(i)}
        title={describe(it)}
      >
        <img src={thumbUrl(it.id, it.hash)} alt={it.filename} loading="lazy" />
        {bestIds.has(it.id) && <span className="strip-best">★</span>}
        {it.matches === false && <span className="strip-filtered">⊘</span>}
        {it.decision && <span className={'strip-flag ' + it.decision} />}
      </button>
    )
  }

  // A collapsed near-dup set: a stacked card showing its medoid and a ×N count.
  // Clicking it selects the medoid and expands the set inline so its members
  // appear — drilling into one group without leaving the scene.
  const renderGroupTile = (s) => {
    const rep = s.items[0]
    const repIdx = idIndex.get(rep.id)
    const anyDecided = s.items.some((it) => it.decision)
    return (
      <button
        key={'tile-' + s.dup_group}
        className={'strip-grouptile' + (repIdx === sel ? ' active' : '')}
        onClick={() => { selectIdx(repIdx); toggleExpand(s.dup_group) }}
        title={`near-duplicate set · ${s.items.length} photos · click to expand`}
      >
        <img src={thumbUrl(rep.id, rep.hash)} alt={rep.filename} loading="lazy" />
        <span className="strip-count">×{s.items.length}</span>
        {anyDecided && <span className="strip-grouptile-decided" />}
      </button>
    )
  }

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="review-panel" onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <div>
            <b>{title ?? `Duplicate group #${group.dup_group}`}</b>
            <span className="review-sub"> · {view.length} photos · viewing {sel + 1}/{view.length}</span>
            {subExtra}
            {someFiltered && (
              <span className="review-filternote"> · {matchCount}/{view.length} match filter</span>
            )}
          </div>
          <div className="review-actions">
            {showGroupBulk && (
              <>
                <button className="btn primary" onClick={keepBestDeleteRest}>Keep best · delete rest</button>
                <button className="btn" onClick={clearGroup}>Clear</button>
              </>
            )}
            {canGroup && !showList && (
              <button
                className={'btn' + (grouped ? ' active' : '')}
                onClick={() => setGrouped((v) => !v)}
                title="Collapse near-duplicate sets into representative tiles"
              >
                {grouped ? `Ungroup (${view.length})` : `Group dups (${sets.length})`}
              </button>
            )}
            <button className={'btn' + (showList ? ' active' : '')} onClick={() => setShowList((v) => !v)}>
              {showList ? 'Preview' : 'List'}
            </button>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>

        {/* Filmstrip — flat by default; "Group dups" collapses each near-dup
            set into a stacked tile that expands inline on click. */}
        <div className="review-strip">
          {canGroup && grouped ? (
            <>
              {sets.map((s) => (
                expanded.has(s.dup_group) ? (
                  <div
                    className="strip-cluster"
                    key={'set-' + s.dup_group}
                    title={`near-duplicate set · ${s.items.length} photos · click ×N to collapse`}
                    onClick={(e) => { if (e.target === e.currentTarget) toggleExpand(s.dup_group) }}
                  >
                    {s.items.map(renderThumb)}
                    <button
                      className="strip-collapse"
                      onClick={(e) => { e.stopPropagation(); toggleExpand(s.dup_group) }}
                      title="Collapse set"
                    >–</button>
                  </div>
                ) : renderGroupTile(s)
              ))}
              {loose.map(renderThumb)}
            </>
          ) : canGroup ? (
            // Flat, but each near-dup set is wrapped in a subtle "group rail"
            // (shared backing + accent underline) so membership reads at a
            // glance without collapsing or click-through.
            <>
              {sets.map((s) => (
                <div
                  className="strip-flatgroup"
                  key={'fg-' + s.dup_group}
                  title={`near-duplicate set · ${s.items.length} photos`}
                >
                  {s.items.map(renderThumb)}
                </div>
              ))}
              {loose.map(renderThumb)}
            </>
          ) : (
            view.map(renderThumb)
          )}
        </div>

        {/* Large preview of the selected member, or a list of all members */}
        {showList ? (
          <div className="review-list">
            {view.map((it) => {
              const i = idIndex.get(it.id)
              const a = aestheticScore(it)
              return (
                <button
                  key={it.id}
                  className={'review-list-row'
                    + (i === sel ? ' active' : '')
                    + (it.matches === false ? ' filtered' : '')}
                  onClick={() => { selectIdx(i); setShowList(false) }}
                  title="Show in preview"
                >
                  <img className="rl-thumb" src={thumbUrl(it.id, it.hash)} alt={it.filename} loading="lazy" />
                  <div className="rl-main">
                    <div className="rl-top">
                      <span className="rl-name">{it.filename}</span>
                      {bestIds.has(it.id) && <span className="rl-best">★ best</span>}
                      {it.matches === false && <span className="rl-flt">outside filter</span>}
                      <DecisionBadge decision={it.decision} />
                    </div>
                    <div className="rl-scores">
                      Q {fmt(it.combined)} · Sh {fmt(it.sharpness)} · Ae {fmt(a)}
                      {it.portrait != null && ` · Portrait ${fmt(it.portrait)}`}
                    </div>
                    <div className={'rl-cap' + (it.caption ? '' : ' empty')}>
                      {it.caption || 'no description'}
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        ) : (
          <div className="review-hero" onClick={() => onZoom(true)} title="Click to zoom">
            <img key={cur.id} src={fullUrl(cur.id, cur.hash)} alt={cur.filename} />
            {bestIds.has(cur.id) && <span className="badge-best">★ best</span>}
            {cur.matches === false && <span className="badge-filtered">outside filter</span>}
            <DecisionBadge decision={cur.decision} />
            <span className="hero-hint">Click to zoom</span>
          </div>
        )}

        {/* Selected member info + decide */}
        <div className="review-herobar">
          <div className="herobar-info">
            <span className="herobar-name">{cur.filename}</span>
            <div className={'herobar-caption' + (cur.caption ? '' : ' empty')}>
              {cur.caption || 'no description'}
            </div>
            <div className="metric-bars">
              {metrics.map(([label, v]) => (
                <div className="metric" key={label} title={`${label}: ${fmt(v)}`}>
                  <span className="metric-label">{label}</span>
                  <div className="metric-track">
                    <div
                      className="metric-fill"
                      style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%`, background: barColor(v) }}
                    />
                  </div>
                  <span className="metric-val">{fmt(v)}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="herobar-btns">
            {mode === 'scene' && dupSiblings.length > 1 && (
              <button
                className="btn"
                onClick={keepThisDeleteDups}
                title="Keep this frame, delete its near-duplicates in this scene"
              >
                Keep · delete {dupSiblings.length - 1} near-dup{dupSiblings.length - 1 > 1 ? 's' : ''}
              </button>
            )}
            <DecideButtons item={cur} onDecision={onDecision} />
          </div>
        </div>
      </div>

      {full && (
        <Lightbox
          items={view}
          index={sel}
          setIndex={lbSetIndex}
          onDecision={onDecision}
          showStrip
        />
      )}
    </div>
  )
}
