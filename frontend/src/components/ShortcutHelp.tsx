import { useEffect, useRef } from 'react'

const SECTIONS: { title: string; rows: [string[], string][] }[] = [
  {
    title: 'Anywhere',
    rows: [
      [['/'], 'Jump to search'],
      [['?'], 'Show this help'],
      [['Esc'], 'Close the open panel or viewer'],
    ],
  },
  {
    title: 'Photo grid',
    rows: [
      [['←', '↑', '↓', '→'], 'Move between photos'],
      [['Home', 'End'], 'First / last loaded photo'],
      [['PgUp', 'PgDn'], 'Jump a few rows'],
      [['Enter'], 'Open the photo full size'],
      [['I'], 'Show or hide photo details'],
      [['K', '+'], 'Keep (press again to clear)'],
      [['D', '−', 'Del'], 'Mark for Trash (press again to clear)'],
    ],
  },
  {
    title: 'Full-size viewer',
    rows: [
      [['←', '→'], 'Previous / next photo'],
      [['K', '+'], 'Keep'],
      [['D', '−', 'Del'], 'Mark for Trash'],
      [['Scroll'], 'Zoom in and out'],
      [['Double-click'], 'Zoom to detail / back to fit'],
    ],
  },
  {
    title: 'Scene and duplicate review',
    rows: [
      [['←', '→'], 'Move between photos'],
      [['↓', '↑'], 'Expand / collapse a near-duplicate set'],
      [['K', '+'], 'Keep'],
      [['D', '−', 'Del'], 'Mark for Trash'],
      [['N'], 'Next group that needs review'],
    ],
  },
]

// Keyboard reference. Sift is built to be driven from the keyboard, but none of
// the shortcuts were discoverable without reading the source.
export default function ShortcutHelp({ onClose }: { onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === '?') { e.preventDefault(); onClose() }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [onClose])

  return (
    <div className="review-overlay" onClick={onClose}>
      <div className="shortcut-panel" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"
        onClick={(e) => e.stopPropagation()}>
        <div className="review-head">
          <b>Keyboard shortcuts</b>
          <div className="spacer" />
          <button className="btn" ref={closeRef} onClick={onClose}>Close</button>
        </div>
        <div className="shortcut-body">
          {SECTIONS.map((section) => (
            <section key={section.title}>
              <h4>{section.title}</h4>
              <dl>
                {section.rows.map(([keys, what]) => (
                  <div key={what + keys.join()}>
                    <dt>{keys.map((k) => <kbd key={k}>{k}</kbd>)}</dt>
                    <dd>{what}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}
