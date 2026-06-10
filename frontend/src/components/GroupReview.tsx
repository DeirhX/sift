import { useState, useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { thumbUrl, fullUrl } from '../api'
import { fmt, aestheticScore, groupByDup, repFirst, isDeleted } from '../format'
import type { DupSet } from '../format'
import type { GroupedImageItem } from '../api/types'
import type { DecisionFn, BulkDecisionFn, SetLightboxIndex } from '../types'
import Lightbox from './Lightbox'
import DecisionBadge from './DecisionBadge'
import DecideButtons from './DecideButtons'
import RecBadge from './RecBadge'

// A group or scene for review: only the member list and an optional id label
// are needed (Group carries dup_group, Scene carries scene_group).
export interface ReviewGroup {
  items: GroupedImageItem[]
  dup_group?: number
  scene_group?: number
}

interface GroupReviewProps {
  group: ReviewGroup
  onClose: () => void
  onDecision: DecisionFn
  onDecisionsBulk: BulkDecisionFn
  mode?: 'group' | 'scene'
  title?: ReactNode
  subExtra?: ReactNode
  showGroupBulk?: boolean
  selId?: number | null
  zoom?: boolean
  onSelect: (id: number) => void
  onZoom: (open: boolean) => void
  // Immediately move this set's del-marked photos to Trash (recoverable). When
  // provided, a "Delete N now" button appears whenever members are marked del.
  onApplyDeletes?: (ids: number[]) => Promise<void>
  // Start with trashed members visible — used when the app's global filter is set
  // to Trash, so an opened set shows the deleted photos it qualified on.
  defaultShowDeleted?: boolean
  // Start with culled (del-marked, not-yet-trashed) members visible — used when
  // the global Decision filter is 'del', so an opened set shows them.
  defaultShowCulled?: boolean
}

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
  group, onClose, onDecision, onDecisionsBulk,
  mode = 'group', title = null, subExtra = null, showGroupBulk = true,
  selId = null, zoom = false, onSelect, onZoom, onApplyDeletes,
  defaultShowDeleted = false, defaultShowCulled = false,
}: GroupReviewProps) {
  // Two kinds of "removed" member, each hidden by default so the strip reflects
  // your culling live, each with its own reveal toggle:
  //   • CULLED  — marked Del but not yet trashed (a reversible verdict). Hiding
  //     these is what makes marking Del shrink the set immediately.
  //   • DELETED — moved to Trash (trash_state set; an optimistic trash patch sets
  //     it the instant "Delete N now" fires).
  const [showCulled, setShowCulled] = useState(defaultShowCulled)
  const [showDeleted, setShowDeleted] = useState(defaultShowDeleted)
  const culledCount = useMemo(
    () => group.items.filter((it) => it.decision === 'del' && !isDeleted(it)).length,
    [group.items])
  const deletedCount = useMemo(() => group.items.filter(isDeleted).length, [group.items])
  // Hide culled + trashed members, but never render a blank panel: if hiding would
  // empty the set (e.g. every member was just culled), fall back to showing all.
  const items = useMemo(() => {
    let v = group.items
    if (!showDeleted) v = v.filter((it) => !isDeleted(it))
    if (!showCulled) v = v.filter((it) => !(it.decision === 'del' && !isDeleted(it)))
    return v.length ? v : group.items
  }, [group.items, showDeleted, showCulled])

  // Scene mode arranges the strip as a tree: sets first (each contiguous), then
  // loose photos. `view` is that flattened order so the hero, arrows and zoom
  // all navigate it consistently. Clusters are only drawn when the scene
  // actually branches (more than one child) — a lone set collapses to a plain
  // strip, the single-child rule that keeps trivial scenes simple. Each set is
  // reordered so its medoid leads, making the cluster's first thumb its hero.
  const { sets, loose } = useMemo(() => {
    if (mode !== 'scene') {
      return { sets: [] as DupSet<GroupedImageItem>[], loose: [] as GroupedImageItem[] }
    }
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
  const idIndex = useMemo(() => new Map(view.map((it, i) => [it.id, i] as const)), [view])

  // Per-photo keep/delete *recommendation* (a hint, never auto-applied). The
  // logic mirrors "keep best · delete rest": the representative (the ★ medoid)
  // is the suggested keep, its peers the suggested deletes. In scene mode this is
  // scoped PER near-duplicate set — a scene's loose, one-off shots aren't
  // redundant, so they get no recommendation at all.
  const recById = useMemo(() => {
    const m = new Map<number, 'keep' | 'del'>()
    if (mode === 'scene') {
      for (const s of sets) s.items.forEach((it, i) => m.set(it.id, i === 0 ? 'keep' : 'del'))
    } else {
      view.forEach((it, i) => m.set(it.id, i === 0 ? 'keep' : 'del'))
    }
    return m
  }, [mode, sets, view])

  // Selection + zoom are controlled by the app (so they live in the URL and
  // browser history). `selId` null → show the default hero (view[0], the
  // medoid). `full` mirrors the `zoom` prop. Helpers translate the existing
  // index-based call sites back to id-based callbacks.
  const sel = (() => {
    const i = selId != null ? idIndex.get(selId) : undefined
    return i == null ? 0 : i
  })()
  const full = !!zoom
  const selectIdx = (i: number | undefined) => {
    if (i == null || i < 0 || i >= view.length) return
    onSelect(view[i].id)
  }
  // Marking the on-screen photo "Del" live-culls it from the strip, which would
  // otherwise drop the controlled selection (selId no longer in `view`) and snap
  // the hero back to the first photo. Pre-advance to the neighbour that slides
  // into its slot — the next photo, or the previous one if it was last — so
  // culling marches forward. Del on a thumb that isn't the current selection, or
  // any other verdict, leaves the selection untouched.
  const decide: DecisionFn = (item, decision) => {
    if (decision === 'del' && !showCulled && !isDeleted(item)) {
      const i = idIndex.get(item.id)
      if (i != null && i === sel) {
        const nextId = view[i + 1]?.id ?? view[i - 1]?.id
        if (nextId != null) onSelect(nextId)
      }
    }
    onDecision(item, decision)
  }
  const [showList, setShowList] = useState(false)
  // Ids in this set marked del but NOT yet trashed — the candidates for an
  // immediate Trash. Read from group.items (not the filtered view) so culled
  // members still hidden by the live-cull remain trashable, and so an already-
  // trashed member (visible via "Show deleted") is never re-trashed.
  const delIds = useMemo(
    () => group.items.filter((it) => it.decision === 'del' && !isDeleted(it)).map((it) => it.id),
    [group.items])
  const [applying, setApplying] = useState(false)
  const [applyErr, setApplyErr] = useState<string | null>(null)
  const applyDeletes = async () => {
    if (!onApplyDeletes || !delIds.length || applying) return
    setApplying(true)
    setApplyErr(null)
    try {
      await onApplyDeletes(delIds)
    } catch (e) {
      setApplyErr(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setApplying(false)
    }
  }
  // Scene strip is grouped by default (near-dup sets collapsed to ×N tiles);
  // `expanded` tracks which collapsed sets are opened inline. For non-scene
  // callers canGroup is false, so this is inert.
  const [grouped, setGrouped] = useState(canGroup)
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set())
  const toggleExpand = (gid: number) => setExpanded((prev) => {
    const next = new Set(prev)
    if (next.has(gid)) next.delete(gid); else next.add(gid)
    return next
  })
  // A scene with exactly one near-dup set has nothing to disambiguate by
  // collapsing — open it on arrival so you see its frames, not a lone ×N tile.
  // Keyed on `sets` (a fresh ref per scene), so a later manual collapse stands.
  useEffect(() => {
    if (sets.length === 1) setExpanded(new Set([sets[0].dup_group]))
  }, [sets])

  // Tooltip / list text for a member: name, key scores, then the caption.
  // \n works in title tooltips; the list renders the same parts as elements.
  const describe = (it: GroupedImageItem): string => {
    const parts = [it.filename,
      `Q ${fmt(it.combined)} · Sh ${fmt(it.sharpness)} · Ae ${fmt(aestheticScore(it))}`]
    if (it.portrait != null) parts.push(`Portrait ${fmt(it.portrait)}`)
    if (it.caption) parts.push(it.caption)
    if (it.matches === false) parts.push('(outside filter)')
    return parts.join('\n')
  }

  // Per near-dup set: how many frames pass the active filters vs. its full size,
  // so the collapse/expand badge reflects the filter the same way the header
  // does ("N of M") instead of always advertising the gross set size.
  const setMatch = (s: DupSet<GroupedImageItem>): number =>
    s.items.filter((it) => it.matches !== false).length
  const setLabel = (s: DupSet<GroupedImageItem>): string => {
    const m = setMatch(s)
    return m < s.items.length ? `${m} of ${s.items.length}` : `${s.items.length}`
  }

  // Set size the way the server (and the overview pile) count it: every member
  // vs. the subset passing the active filters. Read from the full member list so
  // the header's "N of M" matches the pile exactly, no matter which members the
  // strip is currently hiding. The strip still dims non-matching members (see
  // renderThumb) so the full set stays visible for context.
  const memberTotal = group.items.length
  const memberMatch = group.items.filter((it) => it.matches !== false).length
  const partial = memberMatch < memberTotal
  // Same "N of M" for the flat (ungrouped) strip, counted over what it renders.
  const viewMatch = view.filter((it) => it.matches !== false).length
  const viewLabel = viewMatch < view.length ? `${viewMatch} of ${view.length}` : `${view.length}`

  // Keyboard: arrows switch members, k/d decide, Esc closes — but only when
  // the full-size viewer isn't open (it handles its own keys).
  useEffect(() => {
    if (full) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight') selectIdx(Math.min(sel + 1, view.length - 1))
      else if (e.key === 'ArrowLeft') selectIdx(Math.max(sel - 1, 0))
      else if (e.key === 'k' || e.key === '+' || e.key === '=') decide(view[sel], 'keep')
      else if (e.key === 'd' || e.key === '-') decide(view[sel], 'del')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [full, view, sel, onClose, decide])

  // Follow the selection: when arrow keys move it past the rendered edge of the
  // strip, scroll the active member back into view (horizontal only, no page
  // jump). Re-runs on layout changes (group/expand) since the tile moves too.
  const stripRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    stripRef.current?.querySelector('.active')
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' })
  }, [sel, grouped, expanded])

  const cur = view[sel] ?? view[0]
  const aes = aestheticScore(cur)

  // Metric bars for whatever scores this image has (all on a 0–1 scale).
  const metrics = ([
    ['Quality', cur.combined],
    ['Sharpness', cur.sharpness],
    ['Aesthetic', aes],
    ['Composition', cur.para_composition],
    ['Light', cur.para_light],
    ['Portrait', cur.portrait],
    ['Face sharp', cur.face_sharp],
    ['Expression', cur.face_expr],
  ] as [string, number | null | undefined][]).filter((m): m is [string, number] => m[1] != null)
  // Red (low) → amber → green (high), so quality reads at a glance.
  const barColor = (v: number): string => `hsl(${Math.round(Math.max(0, Math.min(1, v)) * 120)}, 65%, 45%)`

  const keepBestDeleteRest = () => {
    const keepId = view[0]?.id
    onDecisionsBulk(items.map((it) => ({
      id: it.id, hash: it.hash ?? null, decision: it.id === keepId ? 'keep' : 'del',
    })))
  }
  const clearGroup = () => {
    onDecisionsBulk(items.map((it) => ({ id: it.id, hash: it.hash ?? null, decision: null })))
  }

  // The selected photo's near-duplicate siblings (scene mode): keep the current
  // frame and drop just its twins, without touching the rest of the scene — the
  // dedupe workflow, scoped to one branch of the tree.
  const dupSiblings = cur.dup_group == null
    ? [] : items.filter((it) => it.dup_group === cur.dup_group)
  const keepThisDeleteDups = () => {
    onDecisionsBulk(dupSiblings.map((it) => ({
      id: it.id, hash: it.hash ?? null, decision: it.id === cur.id ? 'keep' : 'del',
    })))
  }

  // Adapt Lightbox's setIndex (number to navigate, null to close the zoom).
  const lbSetIndex: SetLightboxIndex = (v) => {
    const n = typeof v === 'function' ? v(sel) : v
    if (n == null) onZoom(false)
    else if (n !== sel) selectIdx(n)
  }

  const renderThumb = (it: GroupedImageItem) => {
    const i = idIndex.get(it.id)
    const rec = recById.get(it.id)
    return (
      <button
        key={it.id}
        className={'strip-thumb'
          + (i === sel ? ' active' : '')
          + (it.matches === false ? ' filtered' : '')
          + (isDeleted(it) ? ' deleted' : '')
          + (it.decision === 'del' ? ' is-del' : '')
          + (it.decision === 'keep' ? ' is-keep' : '')}
        onClick={() => selectIdx(i)}
        title={isDeleted(it) ? it.filename + '\n(in Trash)' : describe(it)}
      >
        <img src={thumbUrl(it.id, it.hash)} alt={it.filename} loading="lazy" />
        {bestIds.has(it.id) && (
          <span className="strip-best" title="Best of this set — the suggested keep">★</span>
        )}
        {isDeleted(it)
          ? <span className="strip-trashed" title="In Trash">🗑</span>
          : it.matches === false && (
            <span className="strip-filtered" title="Outside the current filter — shown for context">⊘</span>
          )}
        {/* Suggestion = a dashed "ghost" verdict bar on the TOP edge, mirroring the
            solid committed bar on the bottom. Same hue, opposite edge + dashed, so a
            suggestion can never be mistaken for a decision — even when both show at
            once (red-dashed top over green-solid bottom = "suggested delete, you kept"). */}
        {rec && (
          <span
            className={'strip-rec ' + rec}
            aria-hidden
            title={rec === 'keep'
              ? 'Suggestion: keep — the lead frame of this near-duplicate set. Not applied; click Keep to commit.'
              : 'Suggestion: delete — a near-duplicate of the lead frame. Not applied; click Delete to commit.'}
          />
        )}
        {it.decision && (
          <span
            className={'strip-flag ' + it.decision}
            title={it.decision === 'keep'
              ? 'Your decision: Keep'
              : 'Your decision: Delete — marked, not yet moved to Trash'}
          />
        )}
      </button>
    )
  }

  // A collapsed near-dup set: the medoid thumb, with an always-visible "▸ N"
  // toggle badge (the count IS the open/close control). Clicking the photo just
  // previews the medoid (no surprise reflow); clicking the badge expands the set
  // inline. The same badge rides the expanded cluster as "▾ N" to collapse, so
  // open/close is one consistent control whose caret encodes the state.
  const renderGroupTile = (s: DupSet<GroupedImageItem>) => {
    const rep = s.items[0]
    const repIdx = idIndex.get(rep.id)
    const anyDecided = s.items.some((it) => it.decision)
    return (
      <div className="strip-collapsed" key={'tile-' + s.dup_group}>
        <button
          className={'strip-grouptile' + (repIdx === sel ? ' active' : '')}
          onClick={() => selectIdx(repIdx)}
          title={setMatch(s) < s.items.length
            ? `Near-duplicate set · ${setMatch(s)} of ${s.items.length} match the current filters · click to preview the lead frame`
            : `near-duplicate set · ${s.items.length} photos · click to preview the lead frame`}
        >
          <img src={thumbUrl(rep.id, rep.hash)} alt={rep.filename} loading="lazy" />
          {anyDecided && (
            <span
              className="strip-grouptile-decided"
              title="Some photos in this set already have a Keep/Delete decision"
            />
          )}
        </button>
        <button
          className="strip-toggle expand"
          aria-expanded={false}
          onClick={() => toggleExpand(s.dup_group)}
          title={setMatch(s) < s.items.length
            ? `Near-duplicate set · ${setMatch(s)} of ${s.items.length} match the current filters · click to show all`
            : `Show all ${s.items.length} photos in this near-duplicate set`}
        >
          <span className="strip-toggle-caret">▸</span>{setLabel(s)}
        </button>
      </div>
    )
  }

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="review-panel" onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <div>
            <b>{title ?? `Duplicate group #${group.dup_group}`}</b>
            <span className="review-sub"> · {partial ? `${memberMatch} of ${memberTotal}` : memberTotal} photos · viewing {sel + 1}/{view.length}</span>
            {subExtra}
            {applyErr && <span className="review-filternote err"> · {applyErr}</span>}
          </div>
          <div className="review-actions">
            {/* ACT cluster — the real work (decide / delete), emphasised. Only
                rendered when there's something to act on. */}
            {(showGroupBulk || (onApplyDeletes && delIds.length > 0)) && (
              <div className="review-actgroup">
                {showGroupBulk && (
                  <>
                    <button className="btn primary" onClick={keepBestDeleteRest}>Keep best · delete rest</button>
                    <button className="btn" onClick={clearGroup}>Clear</button>
                  </>
                )}
                {onApplyDeletes && delIds.length > 0 && (
                  <button
                    className="btn danger"
                    onClick={applyDeletes}
                    disabled={applying}
                    title="Move this scene's photos marked Del to Trash now (recoverable)"
                  >
                    {applying ? 'Deleting…' : `Delete ${delIds.length} now`}
                  </button>
                )}
              </div>
            )}
            {/* VIEW cluster — how the strip is shown/filtered: a quiet segmented
                toggle unit so these read as one group of view options, not as
                more actions. */}
            <div className="review-viewseg">
              {canGroup && !showList && (
                <button
                  className={'btn' + (grouped ? ' active' : '')}
                  onClick={() => setGrouped((v) => !v)}
                  title="Collapse near-duplicate sets into representative tiles"
                >
                  {grouped ? `Ungroup (${viewLabel})` : `Group dups (${sets.length})`}
                </button>
              )}
              {culledCount > 0 && (
                <button
                  className={'btn' + (showCulled ? ' active' : '')}
                  onClick={() => setShowCulled((v) => !v)}
                  title={showCulled
                    ? 'Hide photos you marked Del'
                    : 'Show photos marked Del (hidden as you cull; not yet trashed)'}
                >
                  {showCulled ? 'Hide culled' : `Show culled (${culledCount})`}
                </button>
              )}
              {deletedCount > 0 && (
                <button
                  className={'btn' + (showDeleted ? ' active' : '')}
                  onClick={() => setShowDeleted((v) => !v)}
                  title={showDeleted
                    ? 'Hide photos that are in Trash'
                    : 'Show this set\u2019s photos that have been moved to Trash'}
                >
                  {showDeleted ? 'Hide deleted' : `Show deleted (${deletedCount})`}
                </button>
              )}
              <button className={'btn' + (showList ? ' active' : '')} onClick={() => setShowList((v) => !v)}>
                {showList ? 'Preview' : 'List'}
              </button>
            </div>
            <button className="review-close" onClick={onClose} aria-label="Close" title="Close (Esc)">✕</button>
          </div>
        </div>

        {/* Filmstrip — flat by default; "Group dups" collapses each near-dup
            set into a stacked tile that expands inline on click. */}
        <div className="review-strip" ref={stripRef}>
          {canGroup && grouped ? (
            <>
              {sets.map((s) => (
                expanded.has(s.dup_group) ? (
                  <div
                    className="strip-cluster"
                    key={'set-' + s.dup_group}
                    title={setMatch(s) < s.items.length
                      ? `near-duplicate set · ${setMatch(s)} of ${s.items.length} match the current filters`
                      : `near-duplicate set · ${s.items.length} photos`}
                    onClick={(e) => { if (e.target === e.currentTarget) toggleExpand(s.dup_group) }}
                  >
                    <button
                      className="strip-toggle collapse"
                      aria-expanded={true}
                      onClick={(e) => { e.stopPropagation(); toggleExpand(s.dup_group) }}
                      title={setMatch(s) < s.items.length
                        ? `Collapse this near-duplicate set (${setMatch(s)} of ${s.items.length} match the current filters)`
                        : `Collapse this near-duplicate set (${s.items.length} photos)`}
                    >
                      <span className="strip-toggle-caret">▾</span>{setLabel(s)}
                    </button>
                    {s.items.map(renderThumb)}
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
                  title={setMatch(s) < s.items.length
                    ? `near-duplicate set · ${setMatch(s)} of ${s.items.length} match the current filters`
                    : `near-duplicate set · ${s.items.length} photos`}
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
                    + (it.matches === false ? ' filtered' : '')
                    + (isDeleted(it) ? ' deleted' : '')
                    + (it.decision ? ' dec-' + it.decision : '')}
                  onClick={() => { selectIdx(i); setShowList(false) }}
                  title="Show in preview"
                >
                  <img className="rl-thumb" src={thumbUrl(it.id, it.hash)} alt={it.filename} loading="lazy" />
                  <div className="rl-main">
                    <div className="rl-top">
                      <span className="rl-name">{it.filename}</span>
                      {bestIds.has(it.id) && <span className="rl-best">★ best</span>}
                      <RecBadge rec={recById.get(it.id)} />
                      {isDeleted(it)
                        ? <span className="rl-trashed">🗑 in Trash</span>
                        : it.matches === false && <span className="rl-flt">outside filter</span>}
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
            <RecBadge rec={recById.get(cur.id)} />
            {mode === 'scene' && dupSiblings.length > 1 && (
              <button
                className="btn"
                onClick={keepThisDeleteDups}
                title="Keep this frame, delete its near-duplicates in this scene"
              >
                Keep · delete {dupSiblings.length - 1} near-dup{dupSiblings.length - 1 > 1 ? 's' : ''}
              </button>
            )}
            <DecideButtons item={cur} onDecision={decide} />
          </div>
        </div>
      </div>

      {full && (
        <Lightbox
          items={view}
          index={sel}
          setIndex={lbSetIndex}
          onDecision={decide}
          showStrip
        />
      )}
    </div>
  )
}
