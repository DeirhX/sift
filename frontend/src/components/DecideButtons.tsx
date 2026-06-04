import type { ImageItem } from '../api/types'
import type { DecisionFn } from '../types'

interface DecideButtonsProps {
  item: ImageItem
  onDecision: DecisionFn
  // Extra classes applied to BOTH buttons (alongside the keep/del + active
  // classes), so a caller can opt into its own button chrome — e.g. the
  // lightbox passes 'btn lb-act'. The active keep/del colour is still driven by
  // the parent-scoped `.keep.active` / `.del.active` rule in styles.css.
  className?: string
  keepLabel?: string
  delLabel?: string
}

// The Keep / Delete button pair, highlighting the active decision. Returns a
// fragment so each caller keeps its own wrapping container/styling.
export default function DecideButtons(
  { item, onDecision, className = '', keepLabel = 'Keep', delLabel = 'Delete' }: DecideButtonsProps,
) {
  const extra = className ? ' ' + className : ''
  return (
    <>
      <button
        className={'keep' + extra + (item.decision === 'keep' ? ' active' : '')}
        onClick={() => onDecision(item, 'keep')}
      >{keepLabel}</button>
      <button
        className={'del' + extra + (item.decision === 'del' ? ' active' : '')}
        onClick={() => onDecision(item, 'del')}
      >{delLabel}</button>
    </>
  )
}
