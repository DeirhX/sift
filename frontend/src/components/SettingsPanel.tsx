import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { importDecisions } from '../api'

interface SettingsPanelProps {
  onClose: () => void
  onChange?: () => void
}

// Settings: app-level data operations. Folder management (which used to live
// here as "photo roots") is now first-class in the sidebar's Folders panel —
// reveal permission is derived from the library folders automatically, so
// there's no separate roots list to keep in sync.
export default function SettingsPanel({ onClose, onChange }: SettingsPanelProps) {
  const importRef = useRef<HTMLInputElement>(null)
  const [dataMsg, setDataMsg] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const onImportFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''                    // let the same file be re-picked later
    if (!file) return
    setDataMsg('Importing…')
    try {
      const data = JSON.parse(await file.text())
      const r = await importDecisions(data)
      setDataMsg(`Applied ${r.applied} verdict(s)`
        + (r.unmatched ? `, ${r.unmatched} unmatched` : ''))
      onChange?.()                         // refresh badges/counts in the grid
    } catch (err) {
      setDataMsg(err instanceof Error ? `Import failed: ${err.message}` : 'Import failed')
    }
  }

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <b>Settings</b>
          <div className="spacer" />
          <button className="btn" onClick={onClose}>Close</button>
        </div>

        <div className="settings-body">
          <h4 className="settings-section-title">Data</h4>
          <p className="settings-note">
            Move your keep/delete decisions to or from another library as a JSON file,
            matched by photo content. This is for transferring verdicts between catalogs —
            it is <b>not</b> a backup (the database is snapshotted automatically).
          </p>
          <div className="io-row">
            <a className="btn" href="/api/export"
               style={{ textAlign: 'center', textDecoration: 'none' }}>
              Export decisions
            </a>
            <button className="btn" onClick={() => importRef.current?.click()}>
              Import decisions
            </button>
            <input
              ref={importRef}
              type="file"
              accept="application/json,.json"
              style={{ display: 'none' }}
              onChange={onImportFile}
            />
          </div>
          {dataMsg && <div className="io-msg">{dataMsg}</div>}
        </div>
      </div>
    </div>
  )
}
