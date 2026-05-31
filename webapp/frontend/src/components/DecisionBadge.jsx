// Corner badge showing a photo's keep/del decision. Renders nothing when the
// photo is unmarked.
export default function DecisionBadge({ decision }) {
  if (!decision) return null
  return (
    <span className={'badge-decision ' + decision}>
      {decision === 'keep' ? 'KEEP' : 'DEL'}
    </span>
  )
}
