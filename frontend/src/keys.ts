// Shared keyboard helpers, so every surface (grid, lightbox, review overlay)
// agrees on which keys mean keep / delete — and, crucially, on when a key press
// is NOT ours to handle.
import type { Decision } from './types'

interface KeyLike {
  key: string
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
}

// A browser/OS chord (Ctrl+D bookmark, Ctrl+- zoom out, Cmd+K…) must never be
// read as a photo verdict: zooming the page out would otherwise mark the
// focused photo for Trash.
export const hasModifier = (e: KeyLike): boolean => !!(e.ctrlKey || e.metaKey || e.altKey)

// k / + / = keep · d / - / Delete mark for Trash. Case-insensitive so Caps Lock
// doesn't silently disable the letters. Returns null for anything else,
// including any modified chord.
export function decisionForKey(e: KeyLike): Decision | null {
  if (hasModifier(e)) return null
  switch (e.key) {
    case 'k': case 'K': case '+': case '=': return 'keep'
    case 'd': case 'D': case '-': case 'Delete': return 'del'
    default: return null
  }
}

// True when the event came from a control the user is typing into.
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  return !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName ?? '') || !!el.isContentEditable)
}
