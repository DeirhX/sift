import { useState, useEffect, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { getRoots, addRoot, removeRoot, fsComplete } from '../api'
import type { RootsResponse } from '../api/types'

// Infer the path separator from a sample path (backend may be Windows or POSIX).
const sepOf = (p: string): string => (p.includes('\\') ? '\\' : '/')

interface SettingsPanelProps {
  onClose: () => void
  onChange?: () => void
}

// Manage the photo-root directories that bound the file-reveal feature. These
// persist in the DB (survive restarts and rebuilds) and gate which folders the
// lightbox path breadcrumbs can open. The add field autocompletes against the
// server filesystem, since the browser can't enumerate it itself.
export default function SettingsPanel({ onClose, onChange }: SettingsPanelProps) {
  const [roots, setRoots] = useState<string[] | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [suggest, setSuggest] = useState<string[]>([])
  const [truncated, setTruncated] = useState(false)
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(-1)        // highlighted suggestion index

  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reqSeq = useRef(0)                 // guards against stale async responses
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    getRoots().then((d) => setRoots(d.photo_roots)).catch(() => setRoots([]))
  }, [])

  // Escape closes the panel — unless the autocomplete list is open, in which
  // case the input's own handler swallows Escape to dismiss suggestions first.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape' && !(open && suggest.length)) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, suggest.length, onClose])

  // Keep the highlighted row scrolled into view during keyboard nav.
  useEffect(() => {
    if (hi < 0 || !listRef.current) return
    listRef.current.children[hi]?.scrollIntoView({ block: 'nearest' })
  }, [hi])

  const fetchSuggest = (q: string) => {
    const seq = ++reqSeq.current
    fsComplete(q).then((d) => {
      if (seq !== reqSeq.current) return   // a newer request already fired
      setSuggest(d.entries || [])
      setTruncated(!!d.truncated)
      setOpen(true)
      setHi(-1)
    })
  }

  const onInput = (v: string) => {
    setInput(v)
    setError(null)
    if (debounce.current != null) clearTimeout(debounce.current)
    debounce.current = setTimeout(() => fetchSuggest(v), 150)
  }

  // Pick a suggestion: fill the field, append a separator, and drill into it.
  const pick = (entry: string) => {
    const s = sepOf(entry)
    const next = entry.endsWith(s) ? entry : entry + s
    setInput(next)
    fetchSuggest(next)
  }

  const apply = async (fn: () => Promise<RootsResponse>) => {
    setBusy(true)
    setError(null)
    try {
      const d = await fn()
      setRoots(d.photo_roots)
      onChange?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const add = () => {
    const p = input.trim().replace(/[\\/]+$/, '')   // tolerate a trailing sep
    if (!p) return
    apply(() => addRoot(p)).then(() => {
      setInput('')
      setSuggest([])
      setOpen(false)
    })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (open && suggest.length) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setHi((i) => (i + 1) % suggest.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setHi((i) => (i <= 0 ? suggest.length - 1 : i - 1))
        return
      }
      if (e.key === 'Enter' && hi >= 0) {
        e.preventDefault()
        pick(suggest[hi])
        return
      }
      if (e.key === 'Escape') {
        setOpen(false)
        return
      }
    }
    if (e.key === 'Enter') add()
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
            <div className="root-add-field">
              <input
                type="text"
                value={input}
                disabled={busy}
                autoComplete="off"
                spellCheck={false}
                placeholder="Type a folder path — suggestions appear as you go…"
                onChange={(e) => onInput(e.target.value)}
                onFocus={() => { if (suggest.length) setOpen(true) }}
                onBlur={() => setTimeout(() => setOpen(false), 120)}
                onKeyDown={onKeyDown}
              />
              {open && suggest.length > 0 && (
                <ul className="fs-suggest" ref={listRef}>
                  {suggest.map((s, i) => (
                    <li
                      key={s}
                      className={i === hi ? 'fs-opt active' : 'fs-opt'}
                      onMouseEnter={() => setHi(i)}
                      onMouseDown={(e) => { e.preventDefault(); pick(s) }}
                    >
                      {s}
                    </li>
                  ))}
                  {truncated && <li className="fs-more">…more — keep typing to narrow</li>}
                </ul>
              )}
            </div>
            <button className="btn primary" disabled={busy || !input.trim()} onClick={add}>Add</button>
          </div>
          {error && <div className="settings-error">{error}</div>}
        </div>
      </div>
    </div>
  )
}
