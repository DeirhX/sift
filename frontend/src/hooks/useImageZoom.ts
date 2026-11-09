import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'

export interface ImageZoom {
  // Callback ref for the <img>. A callback ref (not a useRef) so the native,
  // non-passive wheel listener re-attaches when the element is replaced — the
  // scene hero remounts its <img> per photo (key=id), which a plain ref-effect
  // would miss, leaving the listener bound to a detached node.
  ref: (el: HTMLImageElement | null) => void
  // CSS transform to apply to the image (translate + scale).
  transform: string
  // True once zoomed past the minimum (drives cursor + pan gating).
  zoomed: boolean
  onMouseDown: (e: ReactMouseEvent) => void
  onDoubleClick: (e: ReactMouseEvent) => void
  reset: () => void
}

interface ZoomOpts {
  min?: number
  max?: number
  // Whether double-click zooms IN (to 2.5x) when at rest. Off for surfaces where
  // a plain click already does something (the scene hero opens the Lightbox, and
  // that click fires on the first half of a double-click) — there double-click
  // still resets once zoomed, since a click is suppressed while zoomed.
  dblZoom?: boolean
}

// Cursor-anchored mouse-wheel zoom + drag-to-pan for a single <img>.
//
// The wheel listener is attached natively with `{ passive: false }` so it can
// preventDefault and stop the surrounding panel/strip from scrolling while you
// zoom over the image. Zoom keeps the pixel under the cursor stationary. Panning
// is only active once zoomed in; double-click resets (and, when `dblZoom`, zooms
// to 2.5x at the cursor from rest). `resetKey` (the image id) snaps back to fit
// whenever the displayed image changes.
export function useImageZoom(resetKey: unknown, opts: ZoomOpts = {}): ImageZoom {
  const { min = 1, max = 8, dblZoom = true } = opts
  const [scale, setScale] = useState(1)
  const [tx, setTx] = useState(0)
  const [ty, setTy] = useState(0)
  // Mirror live state into a ref so the native wheel/drag handlers (bound once)
  // always read current values without re-binding.
  const st = useRef({ scale, tx, ty })
  st.current = { scale, tx, ty }

  const elRef = useRef<HTMLImageElement | null>(null)
  const wheelCleanup = useRef<(() => void) | undefined>(undefined)

  const reset = useCallback(() => { setScale(1); setTx(0); setTy(0) }, [])
  useEffect(() => { reset() }, [resetKey, reset])

  // Zoom toward a screen point, keeping the pixel under it fixed. With a
  // center transform-origin the element's on-screen center is C + translate, so
  // the rect (which already reflects the current transform) gives us C and the
  // cursor's offset from it without measuring an untransformed box.
  const zoomAt = useCallback((clientX: number, clientY: number, factor: number) => {
    const el = elRef.current
    if (!el) return
    const { scale: s0, tx: t0x, ty: t0y } = st.current
    const s1 = Math.min(max, Math.max(min, s0 * factor))
    if (s1 === s0) return
    if (s1 <= min) { setScale(min); setTx(0); setTy(0); return }
    const rect = el.getBoundingClientRect()
    const dx = clientX - (rect.left + rect.width / 2) + t0x
    const dy = clientY - (rect.top + rect.height / 2) + t0y
    const k = s1 / s0
    setScale(s1)
    setTx(dx * (1 - k) + t0x * k)
    setTy(dy * (1 - k) + t0y * k)
  }, [min, max])

  // Attach/detach the native wheel listener entirely through the callback ref —
  // React drives it with the element on mount and with null on unmount — so it
  // is the single source of truth. (A separate unmount-cleanup effect would,
  // under StrictMode's mount→unmount→mount dev cycle, tear the listener down
  // without the ref re-running to re-add it, silently killing wheel zoom in dev.)
  const ref = useCallback((el: HTMLImageElement | null) => {
    wheelCleanup.current?.()
    wheelCleanup.current = undefined
    elRef.current = el
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      // Exponential so each notch is a constant ratio; sign flips in/out.
      zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0015))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    wheelCleanup.current = () => el.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null)
  const onMove = useCallback((e: MouseEvent) => {
    const d = drag.current
    if (!d) return
    setTx(d.tx + (e.clientX - d.x))
    setTy(d.ty + (e.clientY - d.y))
  }, [])
  const onUp = useCallback(() => {
    drag.current = null
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }, [onMove])
  const onMouseDown = useCallback((e: ReactMouseEvent) => {
    if (st.current.scale <= min) return
    e.preventDefault()
    e.stopPropagation()
    drag.current = { x: e.clientX, y: e.clientY, tx: st.current.tx, ty: st.current.ty }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [min, onMove, onUp])

  const onDoubleClick = useCallback((e: ReactMouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (st.current.scale > min) reset()
    else if (dblZoom) zoomAt(e.clientX, e.clientY, Math.min(max, 2.5) / min)
  }, [min, max, dblZoom, reset, zoomAt])

  return {
    ref,
    transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
    zoomed: scale > min,
    onMouseDown,
    onDoubleClick,
    reset,
  }
}
