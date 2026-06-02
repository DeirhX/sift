import { thumbUrl } from '../api'
import { fmtTimeRange, sceneKeywords } from '../format'
import type { Scene } from '../api/types'

interface ScenePileProps {
  scene: Scene
  onOpen: () => void
  activeTags?: string[]
  onToggleTag?: (tag: string) => void
}

// A rough scene shown as a stack: the best photo on top, a couple fanned
// behind, with the photo count, how many near-duplicate sets it contains, and
// its capture-time range. Clicking opens the scene panel to drill in. Its most
// common photo keywords are shown as chips that double as tag-filter toggles
// (`onToggleTag`), so you can pivot the whole view to a keyword from here.
export default function ScenePile({ scene, onOpen, activeTags = [], onToggleTag }: ScenePileProps) {
  const items = scene.items
  const top = items[0]
  const behind = items.slice(1, 3)

  const kept = items.filter((i) => i.decision === 'keep').length
  const del = items.filter((i) => i.decision === 'del').length
  const undecided = items.length - kept - del

  const when = fmtTimeRange(scene.time_start, scene.time_end)
  const dupSets = scene.dup_sets ?? 0
  const keywords = sceneKeywords(items, 6)

  return (
    <div className="pile scene-pile" onClick={onOpen} title={`Scene of ${items.length} photos`}>
      <div className="pile-stack">
        {behind.map((it, i) => (
          <div key={it.id} className={`pile-card back back-${i + 1}`}>
            <img src={thumbUrl(it.id)} loading="lazy" alt="" />
          </div>
        ))}
        <div className="pile-card top">
          <img src={thumbUrl(top.id)} loading="lazy" alt={top.filename} />
          <span className="pile-count">{items.length} photos</span>
          {del > 0 && <span className="pile-flag del">{del} del</span>}
        </div>
      </div>
      <div className="pile-meta">
        {when && <span className="scene-when" title="Capture time range">{when}</span>}
        <div className="pile-pills">
          {dupSets > 0 && (
            <span className="cpill dup" title="Near-duplicate sets in this scene">
              {dupSets} near-dup set{dupSets > 1 ? 's' : ''}
            </span>
          )}
          {kept > 0 && <span className="cpill keep">{kept} keep</span>}
          {del > 0 && <span className="cpill del">{del} del</span>}
          {undecided > 0 && <span className="cpill left">{undecided} left</span>}
        </div>
      </div>
      {keywords.length > 0 && (
        <div className="scene-tags">
          {keywords.map(({ tag, count }) => (
            <button
              key={tag}
              type="button"
              className={'scene-tag' + (activeTags.includes(tag) ? ' active' : '')}
              title={`${count} photo${count > 1 ? 's' : ''} tagged “${tag}” · click to filter`}
              onClick={(e) => { e.stopPropagation(); onToggleTag?.(tag) }}
            >
              {tag}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
