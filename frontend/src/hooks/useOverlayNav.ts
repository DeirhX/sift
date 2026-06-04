import { useCallback, useEffect, useRef, useState } from 'react'
import { buildSearch, parseState } from '../urlState'
import type { AppState, Filters, View, Nav } from '../urlState'

interface OverlayNavOpts {
  // Current list state, mirrored into the URL alongside the overlay.
  filters: Filters
  view: View
  // The URL-parsed state at first load (stable for the app's lifetime); used to
  // seed the overlay and to plant a base history entry on a deep link.
  initial: AppState
  // List-state setters, driven by Back/Forward (popstate re-derives everything
  // from the URL).
  setFilters: (f: Filters) => void
  setView: (v: View) => void
  // Return keyboard focus to the list when an overlay closes.
  focusGrid: () => void
}

export interface OverlayNav {
  nav: Nav | null
  navigate: (nextNav: Nav | null, push?: boolean) => void
  closeOverlay: () => void
  openImage: (id: number) => void
  openGroup: (refId: number) => void
  openScene: (refId: number) => void
  selectImage: (id: number) => void
  setZoom: (z: boolean) => void
}

// Owns the "what overlay is open" state and all of its browser-history
// choreography. Every overlay transition (open, focus a different photo, zoom,
// close) is a real history entry, so Back walks back through exactly what you
// did. Each entry stores `navDepth`: 0 when an overlay first opens, +1 per
// in-overlay step, so the Close button can unwind the whole overlay in one
// history.go while plain Back peels one step at a time. Extracted from App so
// the component body stays about rendering, not history bookkeeping.
export function useOverlayNav(
  { filters, view, initial, setFilters, setView, focusGrid }: OverlayNavOpts,
): OverlayNav {
  const [nav, setNav] = useState<Nav | null>(initial.nav)

  const writeUrl = useCallback((nextNav: Nav | null, push: boolean) => {
    const url = window.location.pathname + buildSearch(filters, view, nextNav)
    let depth: number | null = null
    if (nextNav) {
      const prev = window.history.state?.navDepth
      const sameOverlay = nav && nav.kind === nextNav.kind && nav.refId === nextNav.refId
      depth = sameOverlay && typeof prev === 'number' ? prev + 1 : 0
    }
    const state = { navDepth: depth }
    if (push) window.history.pushState(state, '', url)
    else window.history.replaceState(state, '', url)
  }, [filters, view, nav])

  const navigate = useCallback((nextNav: Nav | null, push = true) => {
    setNav(nextNav)
    writeUrl(nextNav, push)
  }, [writeUrl])

  // Unwind the entire open overlay back to the pre-overlay entry (so Back from
  // there returns to wherever you were, not back into the overlay).
  const closeOverlay = useCallback(() => {
    const d = window.history.state?.navDepth
    if (typeof d === 'number' && d >= 0) window.history.go(-(d + 1))
    else navigate(null, true)
  }, [navigate])

  const openImage = useCallback((id: number) => navigate({ kind: 'lightbox', imgId: id }), [navigate])
  const openGroup = useCallback((refId: number) => navigate({ kind: 'group', refId, imgId: null, zoom: false }), [navigate])
  const openScene = useCallback((refId: number) => navigate({ kind: 'scene', refId, imgId: null, zoom: false }), [navigate])
  // Focus a different photo / toggle zoom inside the current review overlay,
  // preserving its kind + refId so only the relevant URL bits change.
  const selectImage = useCallback((id: number) => navigate(nav ? { ...nav, imgId: id } : null), [navigate, nav])
  const setZoom = useCallback((z: boolean) => navigate(nav ? { ...nav, zoom: z } : null), [navigate, nav])

  // Browser Back/Forward: re-derive everything from the URL.
  useEffect(() => {
    const onPop = () => {
      const s = parseState(window.location.search)
      setFilters(s.filters)
      setView(s.view)
      setNav(s.nav)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [setFilters, setView])

  // If the app loaded straight into an overlay (deep link / reload / new tab),
  // there's no plain-list entry beneath it for Close to unwind onto — so
  // closeOverlay's history.go(-(d+1)) would no-op (nothing before entry 0) or
  // land on another overlay URL, and the modal would never close. Plant that
  // base entry once: replace the current entry with the list (overlay stripped),
  // then push the overlay back on top at depth 0. Now every overlay has a
  // pre-overlay entry below it, so Close works however it was reached.
  useEffect(() => {
    if (!initial.nav) return
    const path = window.location.pathname
    window.history.replaceState({ navDepth: null }, '',
      path + buildSearch(initial.filters, initial.view, null))
    window.history.pushState({ navDepth: 0 }, '',
      path + buildSearch(initial.filters, initial.view, initial.nav))
  }, [initial])

  // Hand keyboard focus back to the list when an overlay closes, so arrow
  // navigation resumes (works for grid, groups and scenes — all use
  // `.grid-scroll`).
  const prevNavRef = useRef<Nav | null>(nav)
  useEffect(() => {
    if (prevNavRef.current && !nav) setTimeout(focusGrid, 0)
    prevNavRef.current = nav
  }, [nav, focusGrid])

  // Mirror filter + view + nav into the URL. Filter/view edits replace (no
  // history spam); overlay transitions already pushed their own entry above,
  // so here we only canonicalise the string when it drifted, preserving the
  // current entry's navDepth.
  useEffect(() => {
    const next = window.location.pathname + buildSearch(filters, view, nav)
    if (next !== window.location.pathname + window.location.search) {
      window.history.replaceState(window.history.state, '', next)
    }
  }, [filters, view, nav])

  return { nav, navigate, closeOverlay, openImage, openGroup, openScene, selectImage, setZoom }
}
