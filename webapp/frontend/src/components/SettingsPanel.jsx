import { useState, useEffect } from 'react'
import { getRoots, addRoot, removeRoot } from '../api.js'

// Manage the photo-root directories that bound the file-reveal feature. These
// persist in the DB (survive restarts and build_db rebuilds) and gate which
// folders the lightbox path breadcrumbs can open.
export default function SettingsPanel({ onClose, onChange }) {
  const [roots, setRoots] = useState(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getRoots().then((d) => setRoots(d.photo_roots)).catch(() => setRoots([]))
  }, [])

  const apply = async (fn) => {
    setBusy(true)
    setError(null)
    try {
      const d = await fn()
      setRoots(d.photo_roots)
      onChange?.()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const add = () => {
    const p = input.trim()
    if (!p) return
    apply(() => addRoot(p)).then(() => setInput(''))
  }

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <b>Settings · Photo folders</b>
          <div className="spacer" />
          <button className="btn" onClick={onClose}>Close</button>
        </div>

        <div className="settings-body">
          <p className="settings-note">
            Folders the app may open in your file manager (the lightbox path links).
            Reveals are limited to these folders and anything inside them.
          </p>

          {roots == null ? (
            <div className="settings-empty">Loading…</div>
          ) : roots.length === 0 ? (
            <div className="settings-empty">No folders configured — reveal is disabled.</div>
          ) : (
            <ul className="root-list">
              {roots.map((r) => (
                <li key={r} className="root-row">
                  <span className="root-path" title={r}>{r}</span>
                  <button className="root-del" disabled={busy}
                    onClick={() => apply(() => removeRoot(r))} title="Remove folder">×</button>
                </li>
              ))}
            </ul>
          )}

          <div className="root-add">
            <input
              type="text"
              value={input}
              disabled={busy}
              placeholder="Paste a folder path to add…"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') add() }}
            />
            <button className="btn primary" disabled={busy || !input.trim()} onClick={add}>Add</button>
          </div>
          {error && <div className="settings-error">{error}</div>}
        </div>
      </div>
    </div>
  )
}
