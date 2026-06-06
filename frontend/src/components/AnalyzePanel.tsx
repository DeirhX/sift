import { useState, useEffect } from 'react'
import { fetchTasks, startTask, cancelTask } from '../api'
import TaskPanel from './TaskPanel'
import FolderInput from './FolderInput'
import type { TaskSnapshot } from '../api/types'

interface AnalyzeParams {
  folder: string
  recurse: boolean
  no_clip: boolean
  backend: string
  caption: boolean
  faces: boolean
  face_expr: boolean
  no_cache: boolean
  no_scenes: boolean
  dup_threshold: string
  face_min_rel: string
  face_eps: string
  scene_time_gap: string
  scene_sim: string
}

interface AnalyzePanelProps {
  defaultFolder?: string
  onClose: () => void
  onDone?: (task: TaskSnapshot) => void
}

// Modal to re-run `sift analyze` + `sift index` from the browser, streaming
// their live output (tqdm progress included) and showing the exact command.
// The launcher is constrained: known flags only; the folder is the one free
// (server-validated) field.
export default function AnalyzePanel({ defaultFolder, onClose, onDone }: AnalyzePanelProps) {
  const [p, setP] = useState<AnalyzeParams>({
    folder: defaultFolder || '',
    recurse: true,
    no_clip: false,
    backend: 'para',
    caption: false,
    faces: true,
    face_expr: true,
    no_cache: false,
    no_scenes: false,
    dup_threshold: '',
    face_min_rel: '',
    face_eps: '',
    scene_time_gap: '',
    scene_sim: '',
  })
  const [taskId, setTaskId] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showAdv, setShowAdv] = useState(false)

  const set = (patch: Partial<AnalyzeParams>) => setP((v) => ({ ...v, ...patch }))

  // Escape closes the panel. The job (if any) keeps running server-side and
  // re-attaches when the panel is reopened.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Re-attach to any already-running library task on open. The modal is now the
  // lightweight "current task" surface, not only an analyze form.
  useEffect(() => {
    fetchTasks().then((s) => {
      const cur = s.current
      if (cur?.state === 'running') {
        setTaskId(cur.id)
        setRunning(true)
      }
    }).catch(() => {})
  }, [])

  const run = async () => {
    setError(null)
    setRunning(true)
    try {
      const task = await startTask('analyze_library', p as unknown as Record<string, unknown>)
      setTaskId(task.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setRunning(false)
    }
  }

  const runIndex = async () => {
    setError(null)
    setRunning(true)
    try {
      const task = await startTask('index_library')
      setTaskId(task.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setRunning(false)
    }
  }

  const taskDone = (task: TaskSnapshot) => {
    setRunning(false)
    setCancelling(false)
    onDone?.(task)
  }

  const doCancel = async () => {
    if (!taskId || !running) return
    setCancelling(true)
    try {
      await cancelTask(taskId)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setCancelling(false)
    }
  }

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="analyze-panel" onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <b>Library operations</b>
          {running && <span className="analyze-status running">Running…</span>}
          <div className="spacer" />
          <div className="review-head-actions">
            {running && (
              <button className="btn danger" disabled={cancelling} onClick={doCancel}>
                {cancelling ? 'Cancelling…' : 'Cancel run'}
              </button>
            )}
            <button className="btn ghost" onClick={onClose}>Close</button>
          </div>
        </div>

        <div className="analyze-body">
          <div className="analyze-form">
            <section className="af-group">
              <div className="af-group-title">Source</div>
              <label className="af-row">
                <span>Folder</span>
                <FolderInput
                  value={p.folder}
                  disabled={running}
                  onChange={(v) => set({ folder: v })}
                  placeholder="path to image library"
                />
              </label>
              <div className="af-checks">
                <label><input type="checkbox" checked={p.recurse} disabled={running}
                  onChange={(e) => set({ recurse: e.target.checked })} /> Recurse subfolders</label>
              </div>
            </section>

            <section className="af-group">
              <div className="af-group-title">Detect &amp; describe</div>
              <div className="af-checks">
                <label><input type="checkbox" checked={p.faces} disabled={running}
                  onChange={(e) => set({ faces: e.target.checked })} /> Detect faces</label>
                <label className={'af-sub' + (p.faces ? '' : ' af-disabled')}>
                  <input type="checkbox" checked={p.face_expr && p.faces} disabled={running || !p.faces}
                    onChange={(e) => set({ face_expr: e.target.checked })} /> Expression score</label>
                <label><input type="checkbox" checked={p.caption} disabled={running}
                  onChange={(e) => set({ caption: e.target.checked })} /> Captions + tags</label>
              </div>
            </section>

            <section className="af-group">
              <div className="af-group-title">Scoring &amp; scenes</div>
              <div className="af-checks">
                <label><input type="checkbox" checked={p.no_clip} disabled={running}
                  onChange={(e) => set({ no_clip: e.target.checked })} /> Skip aesthetic (fast)</label>
              </div>
              <label className={'af-row' + (p.no_clip ? ' af-disabled' : '')}>
                <span>Aesthetic backend</span>
                <select value={p.backend} disabled={running || p.no_clip}
                  onChange={(e) => set({ backend: e.target.value })}>
                  <option value="para">para</option>
                  <option value="clip-iqa">clip-iqa</option>
                  <option value="both">both</option>
                </select>
              </label>
              <div className="af-checks">
                <label><input type="checkbox" checked={p.no_scenes} disabled={running}
                  onChange={(e) => set({ no_scenes: e.target.checked })} /> Skip scenes</label>
              </div>
            </section>

            <section className="af-group">
              <div className="af-group-title">Cache</div>
              <div className="af-checks">
                <label><input type="checkbox" checked={p.no_cache} disabled={running}
                  onChange={(e) => set({ no_cache: e.target.checked })} /> Re-score all (no cache)</label>
              </div>
            </section>

            <div className="af-advanced">
              <button className="link-btn" onClick={() => setShowAdv((v) => !v)}>
                {showAdv ? 'hide advanced' : 'advanced…'}
              </button>
              {showAdv && (
                <div className="af-adv">
                  <label className="af-row"><span>Dup threshold</span>
                    <input type="number" min="0" max="64" value={p.dup_threshold} disabled={running}
                      placeholder="6" onChange={(e) => set({ dup_threshold: e.target.value })} /></label>
                  <label className="af-row"><span>Face min rel</span>
                    <input type="number" step="0.01" min="0" max="1" value={p.face_min_rel} disabled={running}
                      placeholder="0.04" onChange={(e) => set({ face_min_rel: e.target.value })} /></label>
                  <label className="af-row"><span>Face eps</span>
                    <input type="number" step="0.01" min="0.05" max="1.5" value={p.face_eps} disabled={running}
                      placeholder="0.50" onChange={(e) => set({ face_eps: e.target.value })} /></label>
                  <label className="af-row"><span>Scene gap min</span>
                    <input type="number" step="1" min="1" max="1440" value={p.scene_time_gap} disabled={running}
                      placeholder="60" onChange={(e) => set({ scene_time_gap: e.target.value })} /></label>
                  <label className="af-row"><span>Scene sim</span>
                    <input type="number" step="0.01" min="0" max="1" value={p.scene_sim} disabled={running}
                      placeholder="0.85" onChange={(e) => set({ scene_sim: e.target.value })} /></label>
                </div>
              )}
            </div>

            <div className="af-actions">
              <button className="btn primary" disabled={running} onClick={run}>Run analysis + index</button>
              <button className="btn" disabled={running} onClick={runIndex}>Re-index only</button>
            </div>
            {error && <div className="af-error">{error}</div>}
            <div className="af-note">
              Runs local tasks from the browser; progress keeps streaming if this panel is reopened.
            </div>
          </div>

          <TaskPanel taskId={taskId} title="Library task" showCancel={false} onDone={taskDone} />
        </div>
      </div>
    </div>
  )
}
