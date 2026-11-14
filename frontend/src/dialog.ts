// In-app replacements for window.confirm / window.prompt. Same call shape
// (await a yes/no or a string) but rendered by <Dialogs/> in the app's own
// style: a clear title, the exact consequence, a verb on the button, and a
// danger treatment for the one truly irreversible action.
import { useSyncExternalStore } from 'react'

export interface ConfirmOptions {
  title: string
  body?: string
  // A path or other literal shown in monospace under the body.
  detail?: string
  confirmLabel?: string
  cancelLabel?: string
  // Irreversible: red button, and Cancel (not Confirm) gets initial focus.
  danger?: boolean
}

export interface PromptOptions {
  title: string
  body?: string
  label: string
  placeholder?: string
  initial?: string
  confirmLabel?: string
}

export type DialogRequest =
  | ({ kind: 'confirm'; id: number } & ConfirmOptions)
  | ({ kind: 'prompt'; id: number } & PromptOptions)

type Resolver = (value: boolean | string | null) => void

let queue: DialogRequest[] = []
const resolvers = new Map<number, Resolver>()
const listeners = new Set<() => void>()
let nextId = 1

function emit(): void {
  for (const l of listeners) l()
}

function open<T extends boolean | string | null>(req: DialogRequest): Promise<T> {
  return new Promise<T>((resolve) => {
    resolvers.set(req.id, resolve as Resolver)
    queue = [...queue, req]
    emit()
  })
}

// Resolves true when confirmed, false when cancelled or dismissed.
export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  return open<boolean>({ kind: 'confirm', id: nextId++, ...options })
}

// Resolves the entered text (possibly empty), or null when cancelled.
export function promptDialog(options: PromptOptions): Promise<string | null> {
  return open<string | null>({ kind: 'prompt', id: nextId++, ...options })
}

export function settleDialog(id: number, value: boolean | string | null): void {
  const resolve = resolvers.get(id)
  if (!resolve) return
  resolvers.delete(id)
  queue = queue.filter((d) => d.id !== id)
  emit()
  resolve(value)
}

// Cancel everything that is open (used by tests between cases).
export function dismissAllDialogs(): void {
  for (const d of [...queue]) settleDialog(d.id, d.kind === 'confirm' ? false : null)
}

export const isDialogOpen = (): boolean => queue.length > 0

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

const current = (): DialogRequest | null => queue[0] ?? null

// The dialog to show now (requests queue; one is visible at a time).
export function useDialog(): DialogRequest | null {
  return useSyncExternalStore(subscribe, current, current)
}

export const plural = (n: number, one: string, many = one + 's'): string =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`
