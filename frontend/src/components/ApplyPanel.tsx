import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchApplyStatus, fetchTasks, startTask } from '../api'
import TaskPanel from './TaskPanel'
import type { TaskSnapshot } from '../api/types'

interface ApplyPanelProps {
  onTaskDone?: (task: TaskSnapshot) => void
}

// Sidebar panel that moves 'del'-marked files into the app-managed _trash/
// folder, restores them, or permanently empties Trash on demand.
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
          && ['apply_decisions', 'trash_decisions', 'undo_apply',
            'restore_trash', 'empty_trash'].includes(cur.type)) {
        setTaskId(cur.id)
        setBusy(true)
      }
    }).catch(() => {})
  }, [])

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['applyStatus'] })
    qc.invalidateQueries({ predicate: (q) => ['images', 'groups', 'scenes'].includes(q.queryKey[0] as string) })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }

  const doApply = async () => {
    if (!s?.pending) return
    if (!window.confirm(
      `Move ${s.pending} photo(s) marked Del into Trash:\n${s.trash_dir ?? s.rejected_dir}\n\n` +
      `Nothing is permanently deleted until you empty Trash.`)) return
    setBusy(true); setMsg(null)
    try {
      const task = await startTask('trash_decisions')
      setTaskId(task.id)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Apply failed.')
      setBusy(false)
      refresh()
    }
  }

  const doUndo = async () => {
    if (!s?.applied) return
    if (!window.confirm(`Restore ${s.applied} file(s) from Trash?`)) return
    setBusy(true); setMsg(null)
    try {
      const task = await startTask('restore_trash')
      setTaskId(task.id)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Undo failed.')
      setBusy(false)
      refresh()
    }
  }

  const doEmptyTrash = async () => {
    if (!s?.applied) return
    if (!window.confirm(
      `Permanently delete ${s.applied} file(s) from Trash?\n\n` +
      `This cannot be undone. Yes, this is the scary button.`)) return
    setBusy(true); setMsg(null)
    try {
      const task = await startTask('empty_trash')
      setTaskId(task.id)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Empty Trash failed.')
      setBusy(false)
      refresh()
    }
  }

  const taskDone = (task: TaskSnapshot) => {
    setBusy(false)
    const r = task.result ?? {}
    if (task.type === 'apply_decisions' || task.type === 'trash_decisions') {
      setMsg(`Moved to Trash ${r.moved ?? 0}${r.skipped ? `, skipped ${r.skipped}` : ''}.`)
    } else if (task.type === 'undo_apply' || task.type === 'restore_trash') {
      setMsg(`Restored ${r.restored ?? 0}${r.skipped ? `, skipped ${r.skipped}` : ''}.`)
    } else if (task.type === 'empty_trash') {
      setMsg(`Permanently deleted ${r.deleted ?? 0}${r.skipped ? `, skipped ${r.skipped}` : ''}.`)
    }
    refresh()
    onTaskDone?.(task)
  }

  if (!s) return null

  return (
    <div className="apply-panel">
      <label className="group-label">Trash</label>
      <button
        className="btn full danger"
        disabled={busy || !s.pending}
        onClick={doApply}
      >
        {s.pending ? `Move ${s.pending} to Trash` : 'Nothing marked Del'}
      </button>
      {s.applied > 0 && (
        <button className="btn full" disabled={busy} onClick={doUndo}>
          Restore ({s.applied} trashed)
        </button>
      )}
      {s.applied > 0 && (
        <button className="btn full danger" disabled={busy} onClick={doEmptyTrash}>
          Empty Trash ({s.applied})
        </button>
      )}
      {msg && <div className="apply-msg">{msg}</div>}
      <TaskPanel taskId={taskId} title="Trash task" compact onDone={taskDone} />
      <div className="apply-hint">Files move to <code>_trash/</code>; emptying Trash deletes them.</div>
    </div>
  )
}
