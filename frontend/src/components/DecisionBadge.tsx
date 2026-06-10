// Corner badge showing a photo's keep/del decision. Renders nothing when the
// photo is unmarked.
export default function DecisionBadge({ decision }: { decision?: string | null }) {
  if (!decision) return null
  return (
    <span
      className={'badge-decision ' + decision}
      title={decision === 'keep' ? 'Your decision: Keep' : 'Your decision: Delete'}
    >
      {decision === 'keep' ? 'KEEP' : 'DEL'}
    </span>
  )
}
