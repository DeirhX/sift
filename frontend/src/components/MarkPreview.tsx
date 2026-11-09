import type { Decision, ReviewPlan } from '../types'

const label = (mark: Decision | null) => mark === 'keep' ? 'Keep' : mark === 'del' ? 'Mark for Trash' : 'Unmarked'

export default function MarkPreview({ plan, busy, onApply, onCancel }: {
  plan: ReviewPlan
  busy: boolean
  onApply: () => void
  onCancel: () => void
}) {
  const changes = plan.changes.filter(({ previous, decision }) => previous !== decision)
  const keep = changes.filter((item) => item.decision === 'keep').length
  const trash = changes.filter((item) => item.decision === 'del').length
  const clear = changes.length - keep - trash
  const counts = [keep && `${keep} to keep`, trash && `${trash} to mark for Trash`, clear && `${clear} to clear`]
    .filter(Boolean).join(' · ')
  return (
    <section className="mark-preview" aria-label="Preview changes">
      <div className="mark-preview-summary">
        <div>
          <strong>{plan.title}</strong>
          <div>{counts || 'No marks to change.'} {plan.note}</div>
        </div>
        <button className="btn" disabled={busy} onClick={onCancel}>Close preview</button>
        {changes.length > 0 && (
          <button className="btn primary" disabled={busy} onClick={onApply}>Apply marks</button>
        )}
      </div>
      {changes.length > 0 && (
        <details>
          <summary>Show affected photos</summary>
          <ul>
            {changes.map((mark) => (
              <li key={mark.id}><span>{mark.filename}</span><span>{label(mark.previous)} → {label(mark.decision)}</span></li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
