// A tiny app-wide notification store. Failures that used to vanish into the
// console (a mark that didn't save, a face edit the server rejected, a folder
// that wouldn't open) surface here instead, so the UI never silently disagrees
// with what's on disk.
import { useSyncExternalStore } from 'react'

export type ToastKind = 'error' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  message: string
}

const MAX_VISIBLE = 4
const LIFETIME_MS: Record<ToastKind, number> = { error: 8000, info: 4000 }

let toasts: Toast[] = []
let nextId = 1
const listeners = new Set<() => void>()
const timers = new Map<number, ReturnType<typeof setTimeout>>()

function emit(): void {
  for (const l of listeners) l()
}

export function dismissToast(id: number): void {
  const t = timers.get(id)
  if (t) clearTimeout(t)
  timers.delete(id)
  if (!toasts.some((x) => x.id === id)) return
  toasts = toasts.filter((x) => x.id !== id)
  emit()
}

export function clearToasts(): void {
  for (const t of timers.values()) clearTimeout(t)
  timers.clear()
  toasts = []
  emit()
}

function push(kind: ToastKind, message: string): number {
  // Collapse an identical message that's still on screen (e.g. holding a key
  // while the server is down) rather than stacking copies.
  const dup = toasts.find((t) => t.kind === kind && t.message === message)
  if (dup) dismissToast(dup.id)
  const id = nextId++
  toasts = [...toasts, { id, kind, message }].slice(-MAX_VISIBLE)
  timers.set(id, setTimeout(() => dismissToast(id), LIFETIME_MS[kind]))
  emit()
  return id
}

// Turn anything thrown into a short human sentence.
export function errorText(err: unknown, fallback = 'Something went wrong.'): string {
  if (err instanceof TypeError) return 'Can’t reach the Sift server. Is it still running?'
  if (err instanceof Error && err.message) return err.message
  return fallback
}

export const toast = {
  error: (message: string) => push('error', message),
  info: (message: string) => push('info', message),
  // "<what failed>: <why>" from a caught error.
  failure: (what: string, err: unknown) => push('error', `${what} ${errorText(err, '')}`.trim()),
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(subscribe, () => toasts, () => toasts)
}
