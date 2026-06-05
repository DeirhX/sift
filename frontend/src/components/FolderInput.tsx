import { useCallback, useEffect, useRef, useState } from 'react'
import { fsComplete } from '../api'

interface FolderInputProps {
  value: string
  onChange: (v: string) => void
  disabled?: boolean
  placeholder?: string
}

// Path field with server-backed directory autocomplete. The browser can't read
// the filesystem, so suggestions come from /api/fs/complete: an empty value
// lists drives/roots, a trailing separator lists a directory's children, and a
// partial last segment prefix-filters its parent's children. Picking a folder
// appends a separator so the next lookup drills into it — click-to-descend.
export default function FolderInput({ value, onChange, disabled, placeholder }: FolderInputProps) {
  const [items, setItems] = useState<string[]>([])
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(-1)
  const [truncated, setTruncated] = useState(false)
  const focused = useRef(false)
  const seq = useRef(0)            // guards against out-of-order async responses

  const lookup = useCallback((q: string) => {
    const my = ++seq.current
    fsComplete(q).then((r) => {
      if (my !== seq.current || !focused.current) return
      setItems(r.entries)
      setTruncated(r.truncated)
      setOpen(r.entries.length > 0)
      setHi(-1)
    }).catch(() => {
      if (my === seq.current) { setItems([]); setOpen(false) }
    })
  }, [])

  // Debounced lookup while the user types. Skipped unless the field is focused,
  // so a programmatic value change (e.g. defaultFolder) doesn't pop the list.
  useEffect(() => {
    if (!focused.current || disabled) return
    const t = setTimeout(() => lookup(value), 180)
    return () => clearTimeout(t)
  }, [value, disabled, lookup])

  const sepOf = (s: string) => (s.includes('\\') ? '\\' : '/')

  // Last path segment, for a readable label (full path kept as the tooltip).
  const baseName = (s: string) => s.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || s

  const choose = (entry: string) => {
    onChange(entry + sepOf(entry))   // trailing sep → next lookup lists children
    setHi(-1)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open || items.length === 0) return     // let Escape bubble to close modal
    if (e.key === 'ArrowDown') {
      e.preventDefault(); setHi((h) => Math.min(h + 1, items.length - 1)); setOpen(true)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault(); setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter' && hi >= 0) {
      e.preventDefault(); choose(items[hi])
    } else if (e.key === 'Escape') {
      // Close only the dropdown; don't let the modal's Escape handler fire.
      e.preventDefault(); e.stopPropagation(); setOpen(false)
    }
  }

  return (
    <div className="folder-input">
      <input
        type="text"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        onFocus={() => { focused.current = true; lookup(value) }}
        onBlur={() => { focused.current = false; setOpen(false) }}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul className="folder-suggest" role="listbox">
          {items.map((it, i) => (
            <li
              key={it}
              role="option"
              aria-selected={i === hi}
              className={i === hi ? 'active' : ''}
              title={it}
              // mousedown fires before the input's blur, so the click isn't lost.
              onMouseDown={(e) => { e.preventDefault(); choose(it) }}
              onMouseEnter={() => setHi(i)}
            >
              {baseName(it)}
            </li>
          ))}
          {truncated && (
            <li className="folder-suggest-more" aria-disabled="true">
              …more — keep typing to narrow
            </li>
          )}
        </ul>
      )}
    </div>
  )
}
