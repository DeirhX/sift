import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchApplyStatus, fetchTasks, startTask } from '../api'
import TaskPanel from './TaskPanel'
import type { TaskSnapshot } from '../api/types'

interface ApplyPanelProps {
  onTaskDone?: (task: TaskSnapshot) => void
}

// Sidebar panel that moves 'del'-marked files into the library's _rejected/
// folder (reversible) and can undo the last apply. Files are never deleted.
export default function ApplyPanel({ onTaskDone }: ApplyPanelProps) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)

  const status = useQuery({ queryKey: ['applyStatus'], queryFn: fetchApplyStatus })
  const s = status.data

  useEffect(() => {
    fetchTasks().then((r) => {
      const cur = r.current
      if (cur?.state === 'running'
          && (cur.type === 'apply_decisions' || cur.type === 'undo_apply')) {
        setTaskId(cur.id)
        setBusy(true)
      }
    }).catch(() => {})
  }, [])

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
      const task = await startTask('apply_decisions')
      setTaskId(task.id)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Apply failed.')
      setBusy(false)
      refresh()
    }
  }

  const doUndo = async () => {
    if (!s?.applied) return
    if (!window.confirm(`Move ${s.applied} file(s) back to their original locations?`)) return
    setBusy(true); setMsg(null)
    try {
      const task = await startTask('undo_apply')
      setTaskId(task.id)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Undo failed.')
      setBusy(false)
      refresh()
    }
  }

  const taskDone = (task: TaskSnapshot) => {
    setBusy(false)
    const r = task.result ?? {}
    if (task.type === 'apply_decisions') {
      setMsg(`Moved ${r.moved ?? 0}${r.skipped ? `, skipped ${r.skipped}` : ''}.`)
    } else if (task.type === 'undo_apply') {
      setMsg(`Restored ${r.restored ?? 0}${r.skipped ? `, skipped ${r.skipped}` : ''}.`)
    }
    refresh()
    onTaskDone?.(task)
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
      <TaskPanel taskId={taskId} title="Apply task" compact onDone={taskDone} />
      <div className="apply-hint">Files move to <code>_rejected/</code>, never deleted.</div>
    </div>
  )
}
