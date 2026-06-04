import { useEffect, useRef, useState } from 'react'
import { cancelTask } from '../api'
import { useTaskStream } from '../hooks/useTaskStream'
import type { TaskSnapshot } from '../api/types'

interface TaskPanelProps {
  taskId: string | null
  title?: string
  compact?: boolean
  onDone?: (task: TaskSnapshot) => void
}

const terminalStates = new Set(['done', 'failed', 'cancelled', 'abandoned'])

export default function TaskPanel({ taskId, title = 'Task', compact = false, onDone }: TaskPanelProps) {
  const [cancelling, setCancelling] = useState(false)
  const termRef = useRef<HTMLDivElement>(null)
  const { task, lines, partial, progress, connected } = useTaskStream(taskId, onDone)
  const running = task?.state === 'running'
  const pct = Math.round(Math.max(0, Math.min(1, progress?.pct ?? task?.progress ?? 0)) * 100)
  const label = progress?.message || task?.message || task?.state || ''
  const phase = progress?.phase || task?.phase || task?.type

  useEffect(() => {
    const el = termRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines, partial])

  if (!taskId) return null

  const doCancel = async () => {
    if (!taskId || !running) return
    setCancelling(true)
    try {
      await cancelTask(taskId)
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div className={'task-panel' + (compact ? ' compact' : '')}>
      <div className="task-head">
        <div>
          <b>{title}</b>
          {task && <span className={'task-state ' + task.state}> · {task.state}</span>}
          {connected && running && <span className="task-live"> · live</span>}
        </div>
        {running && (
          <button className="btn" disabled={cancelling} onClick={doCancel}>
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        )}
      </div>

      <div className="task-progress-row">
        <div className="task-progress">
          <div className="task-progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="task-pct">{pct}%</span>
      </div>
      <div className="task-meta">
        {phase && <span>{phase}</span>}
        {label && <span>{phase ? ' · ' : ''}{label}</span>}
      </div>

      {task?.commands?.length ? (
        <details className="task-commands">
          <summary>Commands</summary>
          {task.commands.map((cmd, i) => <code key={i}>{cmd}</code>)}
        </details>
      ) : null}

      <div className="analyze-term task-term" ref={termRef}>
        {lines.length === 0 && !partial && (
          <div className="term-empty">Progress will stream here…</div>
        )}
        {lines.map((ln, i) => (
          <div key={i} className={'term-line' + (ln.startsWith('$ ') ? ' term-cmd' : '')}>{ln || '\u00a0'}</div>
        ))}
        {partial && <div className="term-line term-partial">{partial}</div>}
      </div>

      {task && terminalStates.has(task.state) && task.error && (
        <div className="af-error">{task.error}</div>
      )}
    </div>
  )
}

