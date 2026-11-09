import { useState, useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { thumbUrl, fullUrl } from '../api'
import { fmt, aestheticScore, groupActivity, groupByDup, repFirst, isDeleted, rankKeepers } from '../format'
import type { DupSet } from '../format'
import type { GroupedImageItem } from '../api/types'
import type { Decision, DecisionFn, ReviewMark, ReviewPlan, SetLightboxIndex } from '../types'
import Lightbox from './Lightbox'
import DecisionBadge from './DecisionBadge'
import DecideButtons from './DecideButtons'
import RecBadge from './RecBadge'
import BurstCompare from './BurstCompare'
import MarkPreview from './MarkPreview'
import { useImageZoom } from '../hooks/useImageZoom'
import { usePersistentBool } from '../hooks/usePersistentBool'

// Eye / eye-with-slash glyph for the culled/deleted visibility toggles. Drawn
// inline (not an emoji) so it inherits the button's currentColor — amber when a
// hide-filter is active, dim otherwise.
function VisibilityIcon({ hidden }: { hidden: boolean }) {
  return (
    <svg
      className="vis-eye" width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
      <circle cx="12" cy="12" r="3" />
      {hidden && <line x1="3" y1="3" x2="21" y2="21" />}
    </svg>
  )
}

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
  onSaveReview: (changes: ReviewMark[]) => Promise<void>
  onUndoReview?: () => void
  reviewBusy?: boolean
  reviewMessage?: string
  onNextGroup?: () => void
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
  onSplitBefore?: (id: number, hash: string) => Promise<void>
  onOpenGroup?: (dupGroup: number) => void
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
  group, onClose, onDecision,
  onSaveReview, onUndoReview, reviewBusy = false, reviewMessage, onNextGroup,
  mode = 'group', title = null, subExtra = null, showGroupBulk = true,
  selId = null, zoom = false, onSelect, onZoom, onApplyDeletes, onSplitBefore, onOpenGroup,
  defaultShowDeleted = false, defaultShowCulled = false,
}: GroupReviewProps) {
  // Two kinds of "removed" member, each hidden by default so the strip reflects
  // your culling live, each with its own reveal toggle:
  //   • CULLED  — marked Del but not yet trashed (a reversible verdict). Hiding
  //     these is what makes marking Del shrink the set immediately.
  //   • DELETED — moved to Trash (trash_state set; an optimistic trash patch sets
  //     it the instant "Delete N now" fires).
  // Whether culled (Del-marked) members are shown is a GLOBAL, remembered
  // preference: persisted to localStorage so the choice carries across every
  // scene/group you open and across sessions. `defaultShowCulled` only seeds the
  // very first open before any choice is stored. (Deleted/trashed visibility
  // stays a per-open, context-driven default — it's tied to the Trash filter.)
  const [showCulled, setShowCulled] = usePersistentBool('sift.review.showCulled', defaultShowCulled)
  const [showDeleted, setShowDeleted] = useState(defaultShowDeleted)
  const [comparing, setComparing] = useState(false)
  const [plan, setPlan] = useState<ReviewPlan | null>(null)
  const [splitting, setSplitting] = useState(false)
  const [splitError, setSplitError] = useState<string | null>(null)
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
  const groupKeepers = useMemo(() => rankKeepers(group.items), [group.items])
  const activity = groupActivity(group.items)
  const groupKind = groupKeepers.length > 1 && groupKeepers.every((it) => it.hash === groupKeepers[0].hash)
    ? 'Exact copies'
    : groupKeepers.length > 1 && groupKeepers.every((it) => it.dup_cull_safe)
      ? 'Close matches'
      : 'Alternate frames'
  const sceneKeepers = useMemo(
    () => mode === 'scene' ? groupByDup(groupKeepers).sets.map((s) => s.items).filter((s) => s.length > 1) : [],
    [mode, groupKeepers],
  )
  // ★ marks the recommended keeper, independently of the cover frame.
  const bestIds = useMemo(
    () => (mode === 'scene'
      ? new Set(sceneKeepers.map((s) => s[0].id))
      : new Set(groupKeepers.length ? [groupKeepers[0].id] : [])),
    [mode, sceneKeepers, groupKeepers],
  )
  const idIndex = useMemo(() => new Map(view.map((it, i) => [it.id, i] as const)), [view])

  // Per-photo keep/delete *recommendation* (a hint, never auto-applied). The
  // In scene mode recommendations apply only within each comparison set.
  const recById = useMemo(() => {
    const m = new Map<number, 'keep' | 'del'>()
    if (mode === 'scene') {
      for (const s of sceneKeepers) s.forEach((it, i) => m.set(it.id, i === 0 ? 'keep' : 'del'))
    } else {
      groupKeepers.forEach((it, i) => m.set(it.id, i === 0 ? 'keep' : 'del'))
    }
    return m
  }, [mode, sceneKeepers, groupKeepers])

  // Selection + zoom are controlled by the app (so they live in the URL and
  // browser history). `selId` null → show the default hero (view[0], the
  // medoid). `full` mirrors the `zoom` prop. Helpers translate the existing
  // index-based call sites back to id-based callbacks.
  const sel = (() => {
    const i = selId != null ? idIndex.get(selId) : undefined
    return i == null ? 0 : i
  })()
  const full = !!zoom
  const pinned = groupKeepers[0]
  const alternatives = groupKeepers.filter((item) => item.id !== pinned?.id)
  const comparePosition = Math.max(0, alternatives.findIndex((item) => item.id === selId))
  const alternative = alternatives[comparePosition]
  const compareOpen = comparing && pinned != null && alternative != null
  const stepCompare = (delta: number) => {
    const next = alternatives[comparePosition + delta]
    if (next) onSelect(next.id)
  }
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
  // renderThumb) so the full set stays visible for context. Trashed members drop
  // out the instant they're culled (optimistic trash_state patch) so the count
  // reflects a "Delete N now" before the server refetch lands — unless we're
  // explicitly showing deleted (the Trash view), where they're the point.
  const counted = showDeleted ? group.items : group.items.filter((it) => !isDeleted(it))
  const memberTotal = counted.length
  const memberMatch = counted.filter((it) => it.matches !== false).length
  const partial = memberMatch < memberTotal
  // Same "N of M" for the flat (ungrouped) strip, counted over what it renders.
  const viewMatch = view.filter((it) => it.matches !== false).length
  const viewLabel = viewMatch < view.length ? `${viewMatch} of ${view.length}` : `${view.length}`

  // The photos actually visible in the strip, in render order. With grouping on,
  // a COLLAPSED near-dup set contributes only its representative (the single tile
  // you can see); an EXPANDED set contributes every frame. This is the order the
  // ← / → keys walk, so arrow navigation can never land on a hidden member —
  // stepping onto a collapsed set lands on its lone visible photo.
  const visibleView = useMemo(() => {
    if (!(canGroup && grouped)) return view
    const out: GroupedImageItem[] = []
    for (const s of sets) {
      if (expanded.has(s.dup_group)) out.push(...s.items)
      else if (s.items[0]) out.push(s.items[0])
    }
    out.push(...loose)
    return out
  }, [canGroup, grouped, sets, loose, expanded, view])
  const visIndex = useMemo(
    () => new Map(visibleView.map((it, i) => [it.id, i] as const)),
    [visibleView],
  )
  // Which near-dup set (if any) a member id belongs to — lets the keys expand/
  // collapse the set under the selection and map a hidden member to its
  // visible representative.
  const setByMember = useMemo(() => {
    const m = new Map<number, DupSet<GroupedImageItem>>()
    for (const s of sets) for (const it of s.items) m.set(it.id, s)
    return m
  }, [sets])
  // The selection's slot in the visible strip. A selection hidden inside a
  // collapsed set resolves to that set's visible representative.
  const visPosOf = (id: number): number => {
    const i = visIndex.get(id)
    if (i != null) return i
    const repId = setByMember.get(id)?.items[0]?.id
    return (repId != null ? visIndex.get(repId) : undefined) ?? 0
  }

  // Keyboard (the full-size viewer handles its own keys, so skip when zoomed):
  //   ← / →  move between *visible* photos (a collapsed set is one stop: its rep)
  //   ↓ / ↑  expand / collapse the near-dup set under the selection
  //   k/+/=  keep · d/-  delete · Esc closes
  useEffect(() => {
    if (full) return
    const grouping = canGroup && grouped
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (plan) setPlan(null)
        else if (compareOpen) setComparing(false)
        else onClose()
        return
      }
      if (plan || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target as HTMLElement | null)?.tagName ?? '')) return
      if (compareOpen && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault()
        stepCompare(e.key === 'ArrowLeft' ? -1 : 1)
        return
      }
      if (e.key.toLowerCase() === 'n' && onNextGroup) {
        e.preventDefault()
        onNextGroup()
        return
      }
      if (compareOpen && !['k', '+', '=', 'd', '-', 'Delete'].includes(e.key)) return
      const curItem = compareOpen ? alternative : view[sel] ?? view[0]
      if (!curItem) return
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault()
        if (!visibleView.length) return
        const pos = visPosOf(curItem.id)
        const ni = e.key === 'ArrowRight'
          ? Math.min(pos + 1, visibleView.length - 1)
          : Math.max(pos - 1, 0)
        onSelect(visibleView[ni].id)
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!grouping) return
        const s = setByMember.get(curItem.id)
        if (!s) return
        e.preventDefault()
        if (e.key === 'ArrowDown') {
          if (!expanded.has(s.dup_group)) toggleExpand(s.dup_group)
        } else if (expanded.has(s.dup_group)) {
          toggleExpand(s.dup_group)
          // Keep the selection visible after collapsing: snap it to the rep.
          if (s.items[0] && curItem.id !== s.items[0].id) onSelect(s.items[0].id)
        }
      } else if (e.key === 'k' || e.key === '+' || e.key === '=') {
        decide(curItem, 'keep')
      } else if (e.key === 'd' || e.key === '-' || e.key === 'Delete') {
        decide(curItem, 'del')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [full, canGroup, grouped, expanded, visibleView, visIndex, setByMember,
      view, sel, onClose, onSelect, decide, plan, compareOpen, stepCompare, onNextGroup])

  // Follow the selection: when arrow keys move it past the rendered edge of the
  // strip, scroll the active member back into view (horizontal only, no page
  // jump). Re-runs on layout changes (group/expand) since the tile moves too.
  const stripRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    stripRef.current?.querySelector('.active')
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' })
  }, [sel, grouped, expanded])

  const cur = view[sel] ?? view[0]
  const detail = compareOpen ? alternative : cur
  const aes = aestheticScore(detail)
  // Wheel-zoom + drag-pan the inline hero in place (a quick pixel-peek without
  // opening the full viewer); resets to fit whenever the selected photo changes.
  const heroZoom = useImageZoom(cur?.id, { dblZoom: false })

  // Metric bars for whatever scores this image has (all on a 0–1 scale).
  const metrics = ([
    ['Quality', detail.combined],
    ['Sharpness', detail.sharpness],
    ['Aesthetic', aes],
    ['Composition', detail.para_composition],
    ['Light', detail.para_light],
    ['Portrait', detail.portrait],
    ['Face sharp', detail.face_sharp],
    ['Expression', detail.face_expr],
  ] as [string, number | null | undefined][]).filter((m): m is [string, number] => m[1] != null)
  // Red (low) → amber → green (high), so quality reads at a glance.
  const barColor = (v: number): string => `hsl(${Math.round(Math.max(0, Math.min(1, v)) * 120)}, 65%, 45%)`

  const previewKeep = (keep: GroupedImageItem, pool: GroupedImageItem[]) => {
    const changes: ReviewMark[] = pool.flatMap((item) => {
      const decision: Decision = item.id === keep.id ? 'keep' : 'del'
      const previous = item.decision === 'keep' || item.decision === 'del' ? item.decision : null
      return decision === previous && decision !== 'keep' ? [] : [{ id: item.id, path: item.path, hash: item.hash ?? null,
        filename: item.filename, decision, previous }]
    })
    const rival = pool.find((it) => it.hash !== keep.hash)
    const a = keep.keeper_score ?? keep.combined
    const b = rival?.keeper_score ?? rival?.combined
    const close = rival && (a == null || b == null || Math.abs(a - b) < 0.02)
    setPlan({ title: `Keep ${keep.filename}`, changes,
      note: close ? 'Close call. Compare the frames before deciding.' : 'These changes update your marks.' })
  }
  const clearGroup = () => {
    const changes: ReviewMark[] = groupKeepers.flatMap((item) =>
      item.decision === 'keep' || item.decision === 'del'
        ? [{ id: item.id, path: item.path, hash: item.hash ?? null, filename: item.filename,
            decision: null, previous: item.decision }]
        : [])
    setPlan({ title: 'Clear the group’s active marks', changes })
  }

  // The selected photo's near-duplicate siblings (scene mode): keep the current
  // frame and drop just its twins, without touching the rest of the scene — the
  // dedupe workflow, scoped to one branch of the tree.
  const dupSiblings = cur.dup_group == null || isDeleted(cur) || cur.hash == null
    ? [] : groupKeepers.filter((it) => it.dup_group === cur.dup_group)
  const keepThisDeleteDups = () => {
    previewKeep(cur, dupSiblings)
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
            {mode === 'group' && <span className="review-filternote"> · {groupKind}</span>}
            <span className="review-sub"> · {mode === 'group' ? activity.label : `${partial ? `${memberMatch} of ${memberTotal}` : memberTotal} photos`}{!compareOpen && ` · viewing ${sel + 1}/${view.length}`}</span>
            {subExtra}
            {(applyErr || splitError) && <span className="review-filternote err"> · {applyErr || splitError}</span>}
          </div>
          <div className="review-actions">
            {/* ACT cluster — the real work (decide / delete), emphasised. Only
                rendered when there's something to act on. */}
            {(showGroupBulk || (onApplyDeletes && delIds.length > 0)) && (
              <div className="review-actgroup">
                {showGroupBulk && (
                  <>
                    <button className="btn primary" disabled={groupKeepers.length < 2 || reviewBusy}
                      onClick={() => previewKeep(groupKeepers[0], groupKeepers)}>Preview keeper marks</button>
                    <button className="btn" disabled={reviewBusy} onClick={clearGroup}>Clear marks…</button>
                  </>
                )}
                {onApplyDeletes && delIds.length > 0 && (
                  <button
                    className="btn danger"
                    onClick={applyDeletes}
                    disabled={applying}
                    title="Move this scene's photos marked Del to Trash now (recoverable)"
                  >
                    {applying ? 'Moving…' : `Move ${delIds.length} to Trash`}
                  </button>
                )}
              </div>
            )}
            {/* VIEW cluster — how the strip is shown/filtered: a quiet segmented
                toggle unit so these read as one group of view options, not as
                more actions. */}
            <div className="review-viewseg">
              {mode === 'group' && alternatives.length > 0 && (
                <button className={'btn' + (compareOpen ? ' active' : '')}
                  aria-pressed={compareOpen} onClick={() => setComparing((value) => !value)}>Compare</button>
              )}
              {onNextGroup && <button className="btn" title="Next group (N)" onClick={onNextGroup}>Next group</button>}
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
                  className={'btn vis-toggle' + (!showCulled ? ' filtering' : '')}
                  onClick={() => setShowCulled((v) => !v)}
                  aria-label={showCulled ? 'Hide culled photos' : 'Show culled photos'}
                  title={showCulled
                    ? `${culledCount} culled photo${culledCount > 1 ? 's' : ''} (marked Del) shown — click to hide`
                    : `${culledCount} culled photo${culledCount > 1 ? 's' : ''} (marked Del) hidden from this view — click to show`}
                >
                  <VisibilityIcon hidden={!showCulled} />
                  {culledCount} culled {showCulled ? 'shown' : 'hidden'}
                </button>
              )}
              {deletedCount > 0 && (
                <button
                  className={'btn vis-toggle' + (!showDeleted ? ' filtering' : '')}
                  onClick={() => setShowDeleted((v) => !v)}
                  aria-label={showDeleted ? 'Hide deleted photos' : 'Show deleted photos'}
                  title={showDeleted
                    ? `${deletedCount} trashed photo${deletedCount > 1 ? 's' : ''} shown — click to hide`
                    : `${deletedCount} trashed photo${deletedCount > 1 ? 's' : ''} hidden from this view — click to show`}
                >
                  <VisibilityIcon hidden={!showDeleted} />
                  {deletedCount} deleted {showDeleted ? 'shown' : 'hidden'}
                </button>
              )}
              <button className={'btn' + (showList ? ' active' : '')} onClick={() => { setComparing(false); setShowList((v) => !v) }}>
                {showList ? 'Preview' : 'List'}
              </button>
            </div>
            <button className="review-close" onClick={onClose} aria-label="Close" title="Close (Esc)">✕</button>
          </div>
        </div>
        {plan && (
          <MarkPreview plan={plan} busy={reviewBusy}
            onCancel={() => setPlan(null)}
            onApply={() => { void onSaveReview(plan.changes).then(() => setPlan(null)).catch(() => {}) }} />
        )}
        {(reviewMessage || onUndoReview) && (
          <div className="review-notice" role="status">
            <span>{reviewMessage}</span>
            {onUndoReview && <button className="btn" disabled={reviewBusy} onClick={onUndoReview}>Undo marks</button>}
          </div>
        )}

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
        {compareOpen ? (
          <BurstCompare pinned={pinned} alternative={alternative} position={comparePosition}
            count={alternatives.length} onStep={stepCompare} />
        ) : showList ? (
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
          <div
            className={'review-hero' + (heroZoom.zoomed ? ' zoomed' : '')}
            onClick={() => { if (!heroZoom.zoomed) onZoom(true) }}
            title={heroZoom.zoomed ? 'Drag to pan · double-click to reset' : 'Click to open · scroll to zoom'}
          >
            <img
              key={cur.id}
              ref={heroZoom.ref}
              className={heroZoom.zoomed ? 'hero-zoomed' : undefined}
              style={{ transform: heroZoom.transform }}
              src={fullUrl(cur.id, cur.hash)}
              alt={cur.filename}
              draggable={false}
              onMouseDown={heroZoom.onMouseDown}
              onDoubleClick={heroZoom.onDoubleClick}
            />
            {bestIds.has(cur.id) && <span className="badge-best">★ best</span>}
            {cur.matches === false && <span className="badge-filtered">outside filter</span>}
            <DecisionBadge decision={cur.decision} />
            <span className="hero-hint">{heroZoom.zoomed ? 'Scroll to zoom · double-click to reset' : 'Scroll to zoom · click to open'}</span>
          </div>
        )}

        {/* Selected member info + decide */}
        <div className="review-herobar">
          <div className="herobar-info">
            <span className="herobar-name">{detail.filename}</span>
            <div className={'herobar-caption' + (detail.caption ? '' : ' empty')}>
              {detail.caption || 'no description'}
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
            <DecisionBadge decision={(compareOpen ? alternative : cur).decision} detailed />
            <RecBadge rec={recById.get((compareOpen ? alternative : cur).id)} />
            {onOpenGroup && cur.dup_group != null && (
              <button className="btn" onClick={() => onOpenGroup(cur.dup_group!)}>Open full duplicate group</button>
            )}
            {onSplitBefore && cur.hash != null && !isDeleted(cur) && cur.capture_time != null
              && group.items.some((it) => it.capture_time != null && it.capture_time < cur.capture_time!) && (
              <button className="btn" disabled={splitting} onClick={async () => {
                setSplitting(true)
                setSplitError(null)
                try {
                  await onSplitBefore(cur.id, cur.hash!)
                } catch (e) {
                  setSplitError(e instanceof Error ? e.message : 'Could not split scene')
                  setSplitting(false)
                }
              }}>Split scene before this photo</button>
            )}
            {mode === 'scene' && dupSiblings.length > 1 && (
              <button
                className="btn"
                onClick={keepThisDeleteDups}
                title="Keep this frame, delete its near-duplicates in this scene"
              >
                Preview marks for this set
              </button>
            )}
            <DecideButtons item={compareOpen ? alternative : cur} onDecision={decide} />
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
