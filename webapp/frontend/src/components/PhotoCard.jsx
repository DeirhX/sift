import { thumbUrl } from '../api.js'

// One photo tile with face overlays, scores, caption, tags and keep/delete
// actions. thumbH is chosen by the grid to preserve the photo's aspect ratio;
// when it matches the image's proportions the offsets below collapse to zero
// (no crop), and when the grid clamps an extreme ratio the cover-crop math
// keeps face boxes aligned.
export default function PhotoCard({ item, colWidth, thumbH, onOpen, onDecision, personName }) {
  const aes = item.para_aesthetic ?? item.clip_iqa
  const fmt = (v) => (v == null ? '–' : v.toFixed(2))

  // Face boxes are stored in original-image pixel coords; scale to the
  // displayed thumbnail (centered cover-crop for any leftover overflow).
  const iw = item.imgw || 1
  const ih = item.imgh || 1
  const scale = Math.max(colWidth / iw, thumbH / ih)
  const dispW = iw * scale
  const dispH = ih * scale
  const offX = (dispW - colWidth) / 2
  const offY = (dispH - thumbH) / 2

  return (
    <div className="card">
      <div className="thumb-wrap" style={{ height: thumbH }} onClick={onOpen}>
        <img className="thumb" src={thumbUrl(item.id)} loading="lazy" alt={item.filename} />
        {item.faces?.map((f, i) => {
          const [x1, y1, x2, y2] = f.bbox
          const left = x1 * scale - offX
          const top = y1 * scale - offY
          const w = (x2 - x1) * scale
          const h = (y2 - y1) * scale
          return (
            <div
              key={i}
              className="face-box"
              title={personName(f.cluster_id) || `Person ${f.cluster_id}`}
              style={{ left, top, width: w, height: h }}
            />
          )
        })}
        {item.dup_group != null && <span className="badge-dup">dup #{item.dup_group}</span>}
        {item.decision && (
          <span className={'badge-decision ' + item.decision}>
            {item.decision === 'keep' ? 'KEEP' : 'DEL'}
          </span>
        )}
      </div>

      <div className="meta">
        <div className="scores">
          <span>Q <b>{fmt(item.combined)}</b></span>
          <span>Sh <b>{fmt(item.sharpness)}</b></span>
          <span>Ae <b>{fmt(aes)}</b></span>
        </div>
        {item.caption && <div className="caption">{item.caption}</div>}
        {item.tags?.length > 0 && (
          <div className="tags">
            {item.tags.slice(0, 5).map((t) => <span key={t} className="tag">{t}</span>)}
          </div>
        )}
        <div className="actions">
          <button
            className={'keep' + (item.decision === 'keep' ? ' active' : '')}
            onClick={() => onDecision(item, 'keep')}
          >Keep</button>
          <button
            className={'del' + (item.decision === 'del' ? ' active' : '')}
            onClick={() => onDecision(item, 'del')}
          >Delete</button>
        </div>
      </div>
    </div>
  )
}
