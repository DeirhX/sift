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

// "start – end" range, collapsing to a single label when both are the same day.
export function fmtTimeRange(
  start: number | null | undefined,
  end: number | null | undefined,
): string | null {
  const a = fmtTime(start)
  const b = fmtTime(end)
  if (!a) return null
  if (!b || a === b) return a
  return `${a} – ${b}`
}
