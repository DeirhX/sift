// Shared display helpers, so the score formatting and aesthetic fallback are
// defined once instead of being re-declared in every component.

// Two-decimal score, or an en-dash for missing values.
export const fmt = (v) => (v == null ? '–' : v.toFixed(2))

// Aesthetic score, falling back to CLIP-IQA when the PARA score is absent.
export const aestheticScore = (item) => item.para_aesthetic ?? item.clip_iqa

// The representative ("medoid") of a near-duplicate set: the most central frame
// (highest mean cosine to its peers, `dup_central`), with quality as the
// tie-break. Leading a group with this — not the highest-quality frame — keeps
// the hero from being a visual outlier when the group still has some spread.
// Falls back to pure quality when centrality is missing (phash-only groups).
export const representative = (list) => {
  if (!list || list.length === 0) return undefined
  const score = (it) => it.dup_central ?? -1
  return list.reduce((best, it) => {
    const s = score(it), bs = score(best)
    if (s > bs || (s === bs && it.combined > best.combined)) return it
    return best
  }, list[0])
}

// Same list with its representative moved to the front; the rest keep their
// incoming (best-quality-first) order. Used so the hero, ★ and "keep best" all
// agree on the lead frame.
export const repFirst = (list) => {
  if (!list || list.length === 0) return list ?? []
  const rep = representative(list)
  return [rep, ...list.filter((it) => it.id !== rep.id)]
}

// Split a scene's flat item list into its nested near-duplicate sets plus the
// loose members that have no near-dup twin. Sets are ordered by dup_group id;
// items keep the server's best-first order. Pure + tiny so it's unit-testable.
export function groupByDup(items) {
  const map = new Map()
  const loose = []
  for (const it of items ?? []) {
    if (it.dup_group == null) {
      loose.push(it)
    } else {
      if (!map.has(it.dup_group)) map.set(it.dup_group, [])
      map.get(it.dup_group).push(it)
    }
  }
  const sets = [...map.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([dup_group, members]) => ({ dup_group, items: members }))
  return { sets, loose }
}

// Compact capture-time label for a scene. Epoch seconds in, local string out.
export function fmtTime(epoch) {
  if (epoch == null) return null
  const d = new Date(epoch * 1000)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// "start – end" range, collapsing to a single label when both are the same day.
export function fmtTimeRange(start, end) {
  const a = fmtTime(start)
  const b = fmtTime(end)
  if (!a) return null
  if (!b || a === b) return a
  return `${a} – ${b}`
}
