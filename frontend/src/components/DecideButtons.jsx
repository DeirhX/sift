// The Keep / Delete button pair, highlighting the active decision. Returns a
// fragment so each caller keeps its own wrapping container/styling.
export default function DecideButtons({ item, onDecision }) {
  return (
    <>
      <button
        className={'keep' + (item.decision === 'keep' ? ' active' : '')}
        onClick={() => onDecision(item, 'keep')}
      >Keep</button>
      <button
        className={'del' + (item.decision === 'del' ? ' active' : '')}
        onClick={() => onDecision(item, 'del')}
      >Delete</button>
    </>
  )
}
