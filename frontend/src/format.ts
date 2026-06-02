// Shared display helpers, so the score formatting and aesthetic fallback are
// defined once instead of being re-declared in every component.
import type { ImageItem } from './api/types'

// Two-decimal score, or an en-dash for missing values.
export const fmt = (v: number | null | undefined): string => (v == null ? '–' : v.toFixed(2))

// Aesthetic score, falling back to CLIP-IQA when the PARA score is absent.
export const aestheticScore = (
  item: Pick<ImageItem, 'para_aesthetic' | 'clip_iqa'>,
): number | null | undefined => item.para_aesthetic ?? item.clip_iqa

// Quality (0-1) → hue: red (low) → amber → green (high); missing reads as a
// neutral border grey. Shared by the Q pills and stat bars across the card,
// lightbox, and pile components so they can't drift apart.
export const qualityColor = (v: number | null | undefined): string =>
  v == null ? 'var(--border)' : `hsl(${Math.round(Math.max(0, Math.min(1, v)) * 120)}, 58%, 42%)`

// Minimal shape the medoid/grouping helpers need from an image item. Anything
// assignable to ImageItem (or GroupedImageItem) satisfies it.
type RepItem = Pick<ImageItem, 'id' | 'dup_central' | 'combined'>
type DupItem = Pick<ImageItem, 'dup_group'>

// The representative ("medoid") of a near-duplicate set: the most central frame
// (highest mean cosine to its peers, `dup_central`), with quality as the
// tie-break. Leading a group with this — not the highest-quality frame — keeps
// the hero from being a visual outlier when the group still has some spread.
// Falls back to pure quality when centrality is missing (phash-only groups).
export function representative<T extends RepItem = RepItem>(list: T[] | null | undefined): T | undefined {
  if (!list || list.length === 0) return undefined
  const score = (it: T): number => it.dup_central ?? -1
  return list.reduce<T>((best, it) => {
    const s = score(it), bs = score(best)
    if (s > bs || (s === bs && (it.combined ?? 0) > (best.combined ?? 0))) return it
    return best
  }, list[0])
}

// Same list with its representative moved to the front; the rest keep their
// incoming (best-quality-first) order. Used so the hero, ★ and "keep best" all
// agree on the lead frame.
export function repFirst<T extends RepItem = RepItem>(list: T[] | null | undefined): T[] {
  if (!list || list.length === 0) return list ?? []
  const rep = representative(list)
  if (!rep) return list
  return [rep, ...list.filter((it) => it.id !== rep.id)]
}

export interface DupSet<T> {
  dup_group: number
  items: T[]
}

export interface GroupedByDup<T> {
  sets: DupSet<T>[]
  loose: T[]
}

// Split a scene's flat item list into its nested near-duplicate sets plus the
// loose members that have no near-dup twin. Sets are ordered by dup_group id;
// items keep the server's best-first order. Pure + tiny so it's unit-testable.
export function groupByDup<T extends DupItem = DupItem>(items: T[] | null | undefined): GroupedByDup<T> {
  const map = new Map<number, T[]>()
  const loose: T[] = []
  for (const it of items ?? []) {
    if (it.dup_group == null) {
      loose.push(it)
    } else {
      let members = map.get(it.dup_group)
      if (!members) {
        members = []
        map.set(it.dup_group, members)
      }
      members.push(it)
    }
  }
  const sets: DupSet<T>[] = [...map.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([dup_group, members]) => ({ dup_group, items: members }))
  return { sets, loose }
}

// Compact capture-time label for a scene. Epoch seconds in, local string out.
export function fmtTime(epoch: number | null | undefined): string | null {
  if (epoch == null) return null
  const d = new Date(epoch * 1000)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// Time-only label (no date), for the tail of a same-day range.
function fmtClock(epoch: number | null | undefined): string | null {
  if (epoch == null) return null
  const d = new Date(epoch * 1000)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit' })
}

function sameCalendarDay(start: number, end: number): boolean {
  const a = new Date(start * 1000)
  const b = new Date(end * 1000)
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return false
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
}

// "start – end" range. Collapses to one label when both are identical, and drops
// the repeated date from the end when the range stays within a single day
// (e.g. "Jul 4, 2020, 07:19 PM – 07:20 PM"); keeps both dates across days.
export function fmtTimeRange(
  start: number | null | undefined,
  end: number | null | undefined,
): string | null {
  const a = fmtTime(start)
  const b = fmtTime(end)
  if (!a) return null
  if (!b || a === b) return a
  if (start != null && end != null && sameCalendarDay(start, end)) {
    const clock = fmtClock(end)
    if (clock) return `${a} – ${clock}`
  }
  return `${a} – ${b}`
}

// "Hide deletions" filter (decision === 'notdel'): drop members already marked
// for deletion so the set shrinks toward keepers as you cull. The server applies
// the same rule on fetch; this mirror makes culling LIVE — decisions are patched
// into the cache optimistically (no refetch), so without it a just-deleted photo
// would linger. A no-op for every other decision value.
export function applyDecisionHide<T extends { decision?: string | null }>(
  items: T[] | null | undefined,
  decision: string,
): T[] {
  if (decision !== 'notdel' || !items) return items ?? []
  return items.filter((it) => it.decision !== 'del')
}

export interface SceneKeyword {
  tag: string
  count: number
}

// The most common photo keywords (tags) across a scene's members, most-frequent
// first, capped at `limit`. Ties break alphabetically so the order is stable
// across renders. Returns [{ tag, count }] for chip display + filter toggles.
export function sceneKeywords<T extends { tags?: string[] }>(
  items: T[] | null | undefined,
  limit = 6,
): SceneKeyword[] {
  const counts = new Map<string, number>()
  for (const it of items ?? []) {
    for (const tag of it.tags ?? []) {
      counts.set(tag, (counts.get(tag) ?? 0) + 1)
    }
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([tag, count]) => ({ tag, count }))
}
