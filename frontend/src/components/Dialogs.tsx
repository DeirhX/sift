import { useEffect, useRef, useState } from 'react'
import { useDialog, settleDialog } from '../dialog'
import type { DialogRequest } from '../dialog'

// Host for confirmDialog()/promptDialog(). Mounted once at the app root.
export default function Dialogs() {
  const dialog = useDialog()
  if (!dialog) return null
  return <DialogPanel key={dialog.id} dialog={dialog} />
}

function DialogPanel({ dialog }: { dialog: DialogRequest }) {
  const panelRef = useRef<HTMLDivElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [text, setText] = useState(dialog.kind === 'prompt' ? dialog.initial ?? '' : '')
  const textRef = useRef(text)
  textRef.current = text

  const cancel = () => settleDialog(dialog.id, dialog.kind === 'confirm' ? false : null)
  const confirm = () => settleDialog(dialog.id, dialog.kind === 'confirm' ? true : textRef.current)
  const danger = dialog.kind === 'confirm' && !!dialog.danger

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    // An irreversible action must be chosen, never reached by a stray Enter.
    if (dialog.kind === 'prompt') { inputRef.current?.focus(); inputRef.current?.select() }
    else if (danger) cancelRef.current?.focus()
    else confirmRef.current?.focus()

    // Capture phase + stopPropagation: while a dialog is up, no other surface
    // (grid, viewer, review overlay) may react to keys — "d" must not mark a
    // photo behind a confirmation.
    const onKey = (e: KeyboardEvent) => {
      e.stopPropagation()
      if (e.key === 'Escape') { e.preventDefault(); cancel(); return }
      if (e.key === 'Enter') {
        const target = e.target as HTMLElement | null
        // Enter on a focused button activates that button natively.
        if (target?.tagName === 'BUTTON') return
        if (danger) return
        e.preventDefault(); confirm(); return
      }
      if (e.key === 'Tab') {
        const focusable = panelRef.current?.querySelectorAll<HTMLElement>('button, input')
        if (!focusable?.length) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        const active = document.activeElement
        if (e.shiftKey && (active === first || !panelRef.current?.contains(active))) {
          e.preventDefault(); last.focus()
        } else if (!e.shiftKey && (active === last || !panelRef.current?.contains(active))) {
          e.preventDefault(); first.focus()
        }
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      previous?.focus?.()
    }
    // One panel instance per dialog (keyed by id), so this runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const titleId = `dialog-title-${dialog.id}`
  const bodyId = dialog.body ? `dialog-body-${dialog.id}` : undefined
  return (
    <div className="dialog-overlay" onClick={cancel}>
      <div className="dialog-panel" ref={panelRef} role={dialog.kind === 'confirm' ? 'alertdialog' : 'dialog'}
        aria-modal="true" aria-labelledby={titleId} aria-describedby={bodyId}
        onClick={(e) => e.stopPropagation()}>
        <h3 id={titleId}>{dialog.title}</h3>
        {dialog.body && <p id={bodyId}>{dialog.body}</p>}
        {dialog.kind === 'confirm' && dialog.detail && <code className="dialog-detail">{dialog.detail}</code>}
        {dialog.kind === 'prompt' && (
          <label className="dialog-field">
            <span>{dialog.label}</span>
            <input ref={inputRef} type="text" value={text} placeholder={dialog.placeholder}
              onChange={(e) => setText(e.target.value)} />
          </label>
        )}
        <div className="dialog-actions">
          <button className="btn" ref={cancelRef} onClick={cancel}>
            {(dialog.kind === 'confirm' && dialog.cancelLabel) || 'Cancel'}
          </button>
          <button className={'btn ' + (danger ? 'danger' : 'primary')} ref={confirmRef} onClick={confirm}>
            {dialog.confirmLabel || 'OK'}
          </button>
        </div>
      </div>
    </div>
  )
}
