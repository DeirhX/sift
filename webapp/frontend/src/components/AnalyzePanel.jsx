import { useState, useEffect, useRef, useCallback } from 'react'
import { startAnalyze, cancelAnalyze, analyzeStatus, analyzeStreamUrl } from '../api.js'

// Modal to re-run photo_audit.py + build_db.py from the browser, streaming
// their live output (tqdm progress included) and showing the exact command.
// The launcher is constrained: known flags only; the folder is the one free
// (server-validated) field.
export default function AnalyzePanel({ defaultFolder, onClose, onDone }) {
  const [p, setP] = useState({
    folder: defaultFolder || '',
    recurse: true,
    no_clip: false,
    backend: 'para',
    caption: false,
    faces: true,
    face_expr: true,
    no_cache: false,
    dup_threshold: '',
    face_min_rel: '',
    face_eps: '',
  })
  const [lines, setLines] = useState([])
  const [partial, setPartial] = useState('')
  const [state, setState] = useState('idle')   // idle|running|done|failed|cancelled
  const [error, setError] = useState(null)
  const [showAdv, setShowAdv] = useState(false)
  const esRef = useRef(null)
  const termRef = useRef(null)
  const running = state === 'running'

  const set = (patch) => setP((v) => ({ ...v, ...patch }))

  // Escape closes the panel. The job (if any) keeps running server-side and
  // re-attaches when the panel is reopened.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const attachStream = useCallback(() => {
    esRef.current?.close()
    const es = new EventSource(analyzeStreamUrl)
    esRef.current = es
    es.addEventListener('line', (e) => {
      const ln = JSON.parse(e.data)
      setLines((prev) => [...prev, ln])
    })
    es.addEventListener('partial', (e) => setPartial(JSON.parse(e.data)))
    es.addEventListener('end', (e) => {
      const d = JSON.parse(e.data)
      setPartial('')
      es.close()
      if (d.state && d.state !== 'idle') setState(d.state)
      if (d.state === 'done') onDone?.()
    })
    es.onerror = () => { es.close() }
  }, [onDone])

  // Re-attach to an already-running job on open.
  useEffect(() => {
    analyzeStatus().then((s) => {
      if (s.state === 'running') {
        setState('running')
        setLines([])
        attachStream()
      }
    }).catch(() => {})
    return () => esRef.current?.close()
  }, [attachStream])

  // Auto-scroll the terminal as output arrives.
  useEffect(() => {
    const el = termRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines, partial])

  const run = async () => {
    setError(null)
    setLines([])
    setPartial('')
    setState('running')
    try {
      await startAnalyze(p)
      attachStream()
    } catch (e) {
      setError(String(e.message || e))
      setState('failed')
    }
  }

  const cancel = async () => {
    await cancelAnalyze().catch(() => {})
  }

  const statusLabel = {
    idle: '', running: 'Running…', done: 'Done ✓',
    failed: 'Failed', cancelled: 'Cancelled',
  }[state]

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="analyze-panel" onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <b>Re-analyze library</b>
          <span className={'analyze-status ' + state}>{statusLabel}</span>
          <div className="spacer" />
          <button className="btn" onClick={onClose}>Close</button>
        </div>

        <div className="analyze-body">
          <div className="analyze-form">
            <label className="af-row">
              <span>Folder</span>
              <input
                type="text"
                value={p.folder}
                disabled={running}
                onChange={(e) => set({ folder: e.target.value })}
                placeholder="path to image library"
              />
            </label>

            <div className="af-checks">
              <label><input type="checkbox" checked={p.recurse} disabled={running}
                onChange={(e) => set({ recurse: e.target.checked })} /> Recurse subfolders</label>
              <label><input type="checkbox" checked={p.faces} disabled={running}
                onChange={(e) => set({ faces: e.target.checked })} /> Detect faces</label>
              <label className={p.faces ? '' : 'af-disabled'}>
                <input type="checkbox" checked={p.face_expr && p.faces} disabled={running || !p.faces}
                  onChange={(e) => set({ face_expr: e.target.checked })} /> Expression score</label>
              <label><input type="checkbox" checked={p.caption} disabled={running}
                onChange={(e) => set({ caption: e.target.checked })} /> Captions + tags</label>
              <label><input type="checkbox" checked={p.no_cache} disabled={running}
                onChange={(e) => set({ no_cache: e.target.checked })} /> Re-score all (no cache)</label>
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
              </div>
            )}

            <div className="af-actions">
              {!running
                ? <button className="btn primary" onClick={run}>Run analysis</button>
                : <button className="btn" onClick={cancel}>Cancel</button>}
            </div>
            {error && <div className="af-error">{error}</div>}
            <div className="af-note">
              Runs <code>photo_audit.py</code> then <code>build_db.py</code>, then refreshes the view.
            </div>
          </div>

          <div className="analyze-term" ref={termRef}>
            {lines.length === 0 && !partial && (
              <div className="term-empty">Output will stream here…</div>
            )}
            {lines.map((ln, i) => (
              <div key={i} className={'term-line' + (ln.startsWith('$ ') ? ' term-cmd' : '')}>{ln || '\u00a0'}</div>
            ))}
            {partial && <div className="term-line term-partial">{partial}</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
