import { thumbUrl } from '../api.js'
import { fmt, qualityColor } from '../format.js'

// A duplicate group shown as a stack: the best photo on top, up to two
// others fanned behind it, with a count badge and decision summary.
export default function GroupPile({ group, onOpen, focused = false }) {
  const items = group.items
  const top = items[0]
  // Up to two backing cards peeking out behind the top photo.
  const behind = items.slice(1, 3)

  const kept = items.filter((i) => i.decision === 'keep').length
  const del = items.filter((i) => i.decision === 'del').length
  const undecided = items.length - kept - del

  const scores = items.map((i) => i.combined).filter((v) => v != null)
  const best = scores.length ? Math.max(...scores) : null
  const worst = scores.length ? Math.min(...scores) : null

  return (
    <div className={'pile' + (focused ? ' focused' : '')} onClick={onOpen} title={`${items.length} near-duplicates`}>
      <div className="pile-stack">
        {behind.map((it, i) => (
          <div key={it.id} className={`pile-card back back-${i + 1}`}>
            <img src={thumbUrl(it.id, it.hash)} loading="lazy" alt="" />
          </div>
        ))}
        <div className="pile-card top">
          <img src={thumbUrl(top.id, top.hash)} loading="lazy" alt={top.filename} />
          <span className="pile-count">×{items.length}</span>
          {del > 0 && <span className="pile-flag del">{del} del</span>}
        </div>
      </div>
      <div className="pile-meta">
        <span className="q-pill" style={{ background: qualityColor(best) }} title="Composite quality (range across the group)">
          Q {fmt(worst)}{worst !== best ? `–${fmt(best)}` : ''}
        </span>
        <div className="pile-pills">
          {kept > 0 && <span className="cpill keep">{kept} keep</span>}
          {del > 0 && <span className="cpill del">{del} del</span>}
          {undecided > 0 && <span className="cpill left">{undecided} left</span>}
        </div>
      </div>
    </div>
  )
}
