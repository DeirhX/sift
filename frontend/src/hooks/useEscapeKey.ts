import { useEffect } from 'react'

// Call `onEscape` when Escape is pressed anywhere. For simple modal panels;
// confirm dialogs (Dialogs.tsx) swallow keys first, so a panel behind one
// stays open.
export function useEscapeKey(onEscape: () => void): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onEscape() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onEscape])
}
