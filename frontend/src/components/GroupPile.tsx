import { thumbUrl } from '../api'
import { fmt, groupActivity, qualityColor, repFirst } from '../format'
import type { Group } from '../api/types'

// A duplicate group shown as a stack: the representative on top, up to two
// others fanned behind it, with a count badge and decision summary.
export default function GroupPile(
  { group, onOpen, focused = false }: { group: Group; onOpen: () => void; focused?: boolean },
) {
  const items = group.items
  const activity = groupActivity(items)
  const matching = items.filter((it) => it.matches !== false)
  const stack = repFirst(matching.length ? matching : items)
  const top = stack[0]
  // Up to two backing cards peeking out behind the top photo.
  const behind = stack.slice(1, 3)

  const kept = activity.active.filter((i) => i.decision === 'keep').length
  const del = activity.active.filter((i) => i.decision === 'del').length
  const undecided = activity.active.length - kept - del

  const scores = items.map((i) => i.combined).filter((v): v is number => v != null)
  const best = scores.length ? Math.max(...scores) : null
  const worst = scores.length ? Math.min(...scores) : null

  return (
    <div className={'pile' + (focused ? ' focused' : '')} onClick={onOpen} title={activity.label}>
      <div className="pile-stack">
        {behind.map((it, i) => (
          <div key={it.id} className={`pile-card back back-${i + 1}`}>
            <img src={thumbUrl(it.id, it.hash)} loading="lazy" alt="" />
          </div>
        ))}
        <div className="pile-card top">
          <img src={thumbUrl(top.id, top.hash)} loading="lazy" alt={top.filename} />
          {activity.needsReview && <span className="pile-count">Needs review</span>}
        </div>
      </div>
      <div className="pile-group-status">{activity.label}</div>
      <div className="pile-meta">
        <span className="q-pill" style={{ background: qualityColor(best) }} title="Composite quality (range across the group)">
          Q {fmt(worst)}{worst !== best ? `–${fmt(best)}` : ''}
        </span>
        <div className="pile-pills">
          {kept > 0 && <span className="cpill keep">{kept} keep</span>}
          {del > 0 && <span className="cpill del">{del} marked</span>}
          {undecided > 0 && <span className="cpill left">{undecided} left</span>}
        </div>
      </div>
    </div>
  )
}
