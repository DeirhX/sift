// A keep/delete *recommendation* hint — what the tool suggests, NOT a committed
// decision. Styled as a dashed ghost pill so it never reads as the (filled)
// DecisionBadge: a recommendation only guides the eye; nothing is applied until
// the user clicks Keep/Delete. Renders nothing when there's no recommendation
// (e.g. a scene's loose, non-duplicate shots).
export default function RecBadge({ rec }: { rec?: 'keep' | 'del' | null }) {
  if (!rec) return null
  return (
    <span className={'rec-badge ' + rec} title="Suggestion — not applied; click Keep/Delete to decide">
      {rec === 'keep' ? '✓ suggest keep' : '✕ suggest delete'}
    </span>
  )
}
