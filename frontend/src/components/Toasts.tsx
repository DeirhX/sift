import { useToasts, dismissToast } from '../toast'

// Bottom-right stack of transient notices. Errors are announced assertively to
// assistive tech; informational notes politely.
export default function Toasts() {
  const toasts = useToasts()
  if (toasts.length === 0) return null
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={'toast ' + t.kind} role={t.kind === 'error' ? 'alert' : 'status'}>
          <span className="toast-msg">{t.message}</span>
          <button className="toast-x" aria-label="Dismiss" onClick={() => dismissToast(t.id)}>×</button>
        </div>
      ))}
    </div>
  )
}
