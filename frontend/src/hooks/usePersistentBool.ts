import { useCallback, useState } from 'react'

type Updater = boolean | ((prev: boolean) => boolean)

// A boolean UI preference persisted to localStorage, so it survives overlay
// remounts (the review panel mounts fresh on every open) and browser sessions.
// `fallback` applies only when nothing is stored yet, so a contextual first-run
// default can still seed the very first open before the user has chosen.
export function usePersistentBool(key: string, fallback: boolean): [boolean, (v: Updater) => void] {
  const [value, setValue] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw == null ? fallback : raw === '1'
    } catch {
      return fallback
    }
  })
  const set = useCallback((v: Updater) => {
    setValue((prev) => {
      const next = typeof v === 'function' ? v(prev) : v
      try { localStorage.setItem(key, next ? '1' : '0') } catch { /* storage may be unavailable */ }
      return next
    })
  }, [key])
  return [value, set]
}
