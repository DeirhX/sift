// Corner badge showing a photo's keep/del decision. Renders nothing when the
// photo is unmarked.
export default function DecisionBadge({ decision, detailed = false }: { decision?: string | null; detailed?: boolean }) {
  if (!decision) return null
  return (
    <span
      className={'badge-decision ' + decision}
      title={decision === 'keep' ? 'Your decision: Keep' : 'Your decision: Mark for Trash'}
    >
      {detailed ? (decision === 'keep' ? 'Your choice: Keep' : 'Your choice: Marked for Trash')
        : decision === 'keep' ? 'KEEP' : 'MARKED'}
    </span>
  )
}
