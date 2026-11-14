import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchApplyStatus, fetchTasks, startTask } from '../api'
import { useDecisions } from '../hooks/useDecisions'
import TaskPanel from './TaskPanel'
import { confirmDialog, plural } from '../dialog'
import type { TaskSnapshot } from '../api/types'

interface ApplyPanelProps {
  onTaskDone?: (task: TaskSnapshot) => void
  showTrash: boolean
  onOpenTrash: () => void
  onOpenLibrary: () => void
}

// Sidebar panel that moves 'del'-marked files into the app-managed _trash/
// folder, restores them, or permanently empties Trash on demand.
export default function ApplyPanel({ onTaskDone, showTrash, onOpenTrash, onOpenLibrary }: ApplyPanelProps) {
  const qc = useQueryClient()
  const { patchTrashedDel } = useDecisions()
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
    if (!await confirmDialog({
      title: `Move ${plural(s.pending, 'photo')} to Trash?`,
      body: 'Every photo marked for Trash moves into the folder below. You can restore them '
        + 'from the Trash view; nothing is permanently deleted until you empty Trash.',
      detail: s.trash_dir ?? s.rejected_dir ?? undefined,
      confirmLabel: 'Move to Trash',
    })) return
    setBusy(true); setMsg(null)
    // Drop the del-marked photos from every list right away; the task + refetch
    // reconcile. (Only loaded pages are patched, which is all that's on screen.)
    patchTrashedDel()
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
    if (!await confirmDialog({
      title: `Restore ${plural(s.applied, 'file')} from Trash?`,
      body: 'They go back to the folders they came from.',
      confirmLabel: 'Restore',
    })) return
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
    if (!await confirmDialog({
      title: `Permanently delete ${plural(s.applied, 'file')}?`,
      body: 'This empties Trash and removes the files from disk. It cannot be undone.',
      detail: s.trash_dir ?? s.rejected_dir ?? undefined,
      confirmLabel: 'Delete permanently',
      danger: true,
    })) return
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

      {/* Stage 1 — photos you marked Del, still sitting in their folders. The
          recoverable, "main" action, so it gets the primary (not danger) style. */}
      {!showTrash && <div className="trash-stage">
        <div className="trash-stage-head">
          <span className="trash-stage-name">Marked for deletion</span>
          <span className="trash-stage-count">{s.pending}</span>
        </div>
        <button
          className="btn full primary"
          disabled={busy || !s.pending}
          onClick={doApply}
          title={`Move the ${s.pending} photo(s) you marked Del into the _trash/ folder. Recoverable — nothing is deleted from disk yet.`}
        >
          {s.pending ? `Move ${s.pending} to Trash` : 'No photos marked for Trash'}
        </button>
      </div>}

      {/* Stage 2 — files now in _trash/: put them back, or delete for good.
          Only "Empty Trash" is irreversible, so it alone wears the danger style. */}
      {showTrash && (
        <div className="trash-stage">
          <div className="trash-stage-head">
            <span className="trash-stage-name">In Trash</span>
            <span className="trash-stage-count">{s.applied}</span>
          </div>
          <button
            className="btn full"
            disabled={busy || !s.applied}
            onClick={doUndo}
            title="Move the trashed files back to their original locations."
          >
            ← Restore {s.applied}
          </button>
          <button
            className="btn full danger"
            disabled={busy || !s.applied}
            onClick={doEmptyTrash}
            title="Permanently delete the trashed files from disk. This cannot be undone."
          >
            Empty Trash
          </button>
        </div>
      )}

      {msg && <div className="apply-msg">{msg}</div>}
      <TaskPanel taskId={taskId} title="Trash task" compact onDone={taskDone} />
      <div className="apply-hint">
        {showTrash ? <button className="btn full" onClick={onOpenLibrary}>Back to Library</button> : <button
          className="btn full" onClick={onOpenTrash}>Open Trash · {s.applied}</button>}
      </div>
    </div>
  )
}
