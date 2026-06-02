import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchApplyStatus, applyDecisions, undoApply } from '../api'

// Sidebar panel that moves 'del'-marked files into the library's _rejected/
// folder (reversible) and can undo the last apply. Files are never deleted.
export default function ApplyPanel() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const status = useQuery({ queryKey: ['applyStatus'], queryFn: fetchApplyStatus })
  const s = status.data

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['applyStatus'] })
    qc.invalidateQueries({ predicate: (q) => ['images', 'groups'].includes(q.queryKey[0] as string) })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }

  const doApply = async () => {
    if (!s?.pending) return
    if (!window.confirm(
      `Move ${s.pending} photo(s) marked for deletion into:\n${s.rejected_dir}\n\n` +
      `Nothing is permanently deleted — this can be undone.`)) return
    setBusy(true); setMsg(null)
    try {
      const r = await applyDecisions()
      setMsg(`Moved ${r.moved}${r.skipped ? `, skipped ${r.skipped}` : ''}.`)
      refresh()
    } catch {
      setMsg('Apply failed.')
    } finally { setBusy(false) }
  }

  const doUndo = async () => {
    if (!s?.applied) return
    if (!window.confirm(`Move ${s.applied} file(s) back to their original locations?`)) return
    setBusy(true); setMsg(null)
    try {
      const r = await undoApply()
      setMsg(`Restored ${r.restored}${r.skipped ? `, skipped ${r.skipped}` : ''}.`)
      refresh()
    } catch {
      setMsg('Undo failed.')
    } finally { setBusy(false) }
  }

  if (!s) return null

  return (
    <div className="apply-panel">
      <label className="group-label">Apply decisions</label>
      <button
        className="btn full danger"
        disabled={busy || !s.pending}
        onClick={doApply}
      >
        {s.pending ? `Move ${s.pending} to _rejected` : 'Nothing marked for deletion'}
      </button>
      {s.applied > 0 && (
        <button className="btn full" disabled={busy} onClick={doUndo}>
          Undo ({s.applied} moved)
        </button>
      )}
      {msg && <div className="apply-msg">{msg}</div>}
      <div className="apply-hint">Files move to <code>_rejected/</code>, never deleted.</div>
    </div>
  )
}
