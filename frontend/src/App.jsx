import { useState, useCallback, useEffect, useRef } from 'react'
import { useQuery, useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { fetchMeta, fetchImages, fetchGroups, fetchScenes, setDecision as apiSetDecision } from './api.js'
import { DEFAULT_FILTERS, parseState, buildSearch } from './urlState.js'
import Sidebar from './components/Sidebar.jsx'
import PhotoGrid from './components/PhotoGrid.jsx'
import GroupView from './components/GroupView.jsx'
import SceneView from './components/SceneView.jsx'
import GroupReview from './components/GroupReview.jsx'
import ScenePanel from './components/ScenePanel.jsx'
import Lightbox from './components/Lightbox.jsx'
import AnalyzePanel from './components/AnalyzePanel.jsx'
import SettingsPanel from './components/SettingsPanel.jsx'

const PAGE = 60
const GROUP_PAGE = 30
const SCENE_PAGE = 30

// Hydrate initial state from the URL so reloads/shared links restore the view.
const INITIAL = parseState(window.location.search)

export default function App() {
  const [filters, setFilters] = useState(INITIAL.filters)
  const [view, setView] = useState(INITIAL.view)   // 'grid' | 'groups' | 'scenes'
  // The single source of truth for "what overlay is open" (lightbox / group /
  // scene review, the focused photo, and zoom). Mirrored to the URL so Back,
  // shared links and reloads all behave (see urlState.js).
  const [nav, setNav] = useState(INITIAL.nav)
  const [showAnalyze, setShowAnalyze] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const qc = useQueryClient()
  const searchRef = useRef(null)

  const focusGrid = useCallback(() => {
    document.querySelector('.grid-scroll')?.focus()
  }, [])

  // Global shortcut: "/" jumps to the search box (unless already typing or a
  // modal is open). Pairs with the grid's arrow-key navigation.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return
      if (nav != null || showAnalyze || showSettings) return
      e.preventDefault()
      searchRef.current?.focus()
      searchRef.current?.select()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nav, showAnalyze, showSettings])

  // --- Overlay navigation -------------------------------------------------
  // Every overlay transition (open, focus a different photo, zoom, close) is a
  // real history entry, so Back walks back through exactly what you did. Each
  // entry stores `navDepth`: 0 when an overlay first opens, +1 per in-overlay
  // step. The Close button uses that depth to unwind the whole overlay in one
  // go (history.go), while plain Back peels one step at a time.
  const writeUrl = useCallback((nextNav, push) => {
    const url = window.location.pathname + buildSearch(filters, view, nextNav)
    let depth = null
    if (nextNav) {
      const prev = window.history.state?.navDepth
      const sameOverlay = nav && nav.kind === nextNav.kind && nav.refId === nextNav.refId
      depth = sameOverlay && typeof prev === 'number' ? prev + 1 : 0
    }
    const state = { navDepth: depth }
    if (push) window.history.pushState(state, '', url)
    else window.history.replaceState(state, '', url)
  }, [filters, view, nav])

  const navigate = useCallback((nextNav, push = true) => {
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

  const openImage = useCallback((id) => navigate({ kind: 'lightbox', imgId: id }), [navigate])
  const openGroup = useCallback((refId) => navigate({ kind: 'group', refId, imgId: null, zoom: false }), [navigate])
  const openScene = useCallback((refId) => navigate({ kind: 'scene', refId, imgId: null, zoom: false }), [navigate])
  // Focus a different photo / toggle zoom inside the current review overlay,
  // preserving its kind + refId so only the relevant URL bits change.
  const selectImage = useCallback((id) => navigate({ ...nav, imgId: id }), [navigate, nav])
  const setZoom = useCallback((z) => navigate({ ...nav, zoom: z }), [navigate, nav])

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
  }, [])

  // Hand keyboard focus back to the list when an overlay closes, so arrow
  // navigation resumes (works for grid, groups and scenes — all use
  // `.grid-scroll`).
  const prevNavRef = useRef(nav)
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

  const meta = useQuery({ queryKey: ['meta'], queryFn: fetchMeta })

  const images = useInfiniteQuery({
    queryKey: ['images', filters],
    queryFn: ({ pageParam = 0 }) => fetchImages(filters, pageParam, PAGE),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.limit
      return next < last.total ? next : undefined
    },
    enabled: view === 'grid',
  })

  const groups = useInfiniteQuery({
    queryKey: ['groups', filters],
    queryFn: ({ pageParam = 0 }) => fetchGroups(filters, pageParam, GROUP_PAGE),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.limit
      return next < last.total ? next : undefined
    },
    enabled: view === 'groups',
  })

  const scenes = useInfiniteQuery({
    queryKey: ['scenes', filters],
    queryFn: ({ pageParam = 0 }) => fetchScenes(filters, pageParam, SCENE_PAGE),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.limit
      return next < last.total ? next : undefined
    },
    enabled: view === 'scenes',
  })

  const items = images.data?.pages.flatMap((p) => p.items) ?? []
  const total = images.data?.pages[0]?.total ?? 0
  const groupTotal = groups.data?.pages[0]?.total ?? 0
  const sceneTotal = scenes.data?.pages[0]?.total ?? 0

  const people = meta.data?.clusters ?? []

  // Resolve the open overlay's backing record from the loaded query pages. May
  // be null right after a deep-link/reload until the relevant page arrives;
  // the overlay simply waits rather than rendering against missing data.
  const openGroupObj = nav?.kind === 'group'
    ? (groups.data?.pages.flatMap((p) => p.groups) ?? []).find((g) => g.dup_group === nav.refId) || null
    : null
  const openSceneObj = nav?.kind === 'scene'
    ? (scenes.data?.pages.flatMap((p) => p.scenes) ?? []).find((s) => s.scene_group === nav.refId) || null
    : null

  // Adapt the index-based Lightbox (grid) to id-based nav: each move pushes a
  // history step; closing unwinds the overlay.
  const gridLbIndex = nav?.kind === 'lightbox' ? items.findIndex((it) => it.id === nav.imgId) : -1
  const gridLbSetIndex = useCallback((v) => {
    const cur = items.findIndex((it) => it.id === nav?.imgId)
    const n = typeof v === 'function' ? v(cur) : v
    if (n == null) { closeOverlay(); return }
    if (n < 0 || n >= items.length || n === cur) return
    openImage(items[n].id)
  }, [items, nav, closeOverlay, openImage])

  const updateFilter = useCallback((patch) => {
    setFilters((f) => ({ ...f, ...patch }))
  }, [])

  const resetFilters = useCallback(() => setFilters(DEFAULT_FILTERS), [])

  // Refetch the photo/group/scene list queries (roll back a failed decision).
  const invalidateLists = useCallback(() => {
    qc.invalidateQueries({ predicate: (q) => ['images', 'groups', 'scenes'].includes(q.queryKey[0]) })
  }, [qc])

  // After a face/person edit, refetch everything that renders names or counts.
  const refetchPeople = useCallback(() => {
    qc.invalidateQueries({
      predicate: (q) => ['images', 'groups', 'scenes', 'meta'].includes(q.queryKey[0]),
    })
  }, [qc])

  const toggleInList = useCallback((key, value) => {
    setFilters((f) => {
      const cur = f[key]
      return {
        ...f,
        [key]: cur.includes(value)
          ? cur.filter((v) => v !== value)
          : [...cur, value],
      }
    })
  }, [])

  // Optimistically patch a photo's decision in every cached query that holds
  // it — works for both the flat images cache and the nested groups cache.
  const patchDecision = useCallback((id, decision) => {
    qc.setQueriesData(
      { predicate: (q) => ['images', 'groups', 'scenes'].includes(q.queryKey[0]) },
      (data) => {
        if (!data?.pages) return data
        const patchItems = (arr) => arr?.map((it) => (it.id === id ? { ...it, decision } : it))
        return {
          ...data,
          pages: data.pages.map((pg) => ({
            ...pg,
            items: patchItems(pg.items),
            groups: pg.groups?.map((g) => ({ ...g, items: patchItems(g.items) })),
            scenes: pg.scenes?.map((s) => ({ ...s, items: patchItems(s.items) })),
          })),
        }
      },
    )
  }, [qc])

  // Single toggle: clicking the current decision clears it.
  const setDecision = useCallback(async (item, decision) => {
    const next = item.decision === decision ? null : decision
    patchDecision(item.id, next)
    try {
      await apiSetDecision(item.hash, next)
    } catch {
      invalidateLists()
    }
  }, [patchDecision, invalidateLists])

  // Bulk apply (e.g. "keep best, delete rest"). updates: [{id, hash, decision}]
  const setDecisionsBulk = useCallback(async (updates) => {
    updates.forEach((u) => patchDecision(u.id, u.decision))
    try {
      await Promise.all(updates.map((u) => apiSetDecision(u.hash, u.decision)))
    } catch {
      invalidateLists()
    }
  }, [patchDecision, invalidateLists])

  const headerCount = view === 'grid' ? total : view === 'scenes' ? sceneTotal : groupTotal
  const headerLabel = view === 'grid' ? 'photos'
    : view === 'scenes' ? 'scenes' : 'duplicate groups'

  return (
    <div className="app">
      <Sidebar
        meta={meta.data}
        filters={filters}
        updateFilter={updateFilter}
        toggleInList={toggleInList}
        resetFilters={resetFilters}
        total={total}
        onPeopleChange={refetchPeople}
      />
      <div className="main">
        <div className="topbar">
          {view === 'grid' && (
            <input
              className="search"
              type="text"
              placeholder="Search captions…  ( / )"
              ref={searchRef}
              key={filters.q}
              defaultValue={filters.q}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { updateFilter({ q: e.target.value }); focusGrid() }
                else if (e.key === 'Escape') { e.target.blur(); focusGrid() }
              }}
              onBlur={(e) => { if (e.target.value !== filters.q) updateFilter({ q: e.target.value }) }}
            />
          )}
          <span className="result-count">
            {headerCount.toLocaleString()} {headerLabel}
          </span>
          <div className="spacer" />
          <button className="btn" onClick={() => setShowAnalyze(true)} title="Re-run analysis from the web">
            Re-analyze
          </button>
          <button className="btn" onClick={() => setShowSettings(true)} title="Settings">
            Settings
          </button>
          <div className="seg view-toggle">
            <button className={view === 'grid' ? 'active' : ''} onClick={() => setView('grid')}>Grid</button>
            <button className={view === 'scenes' ? 'active' : ''} onClick={() => setView('scenes')}>Scenes</button>
            <button className={view === 'groups' ? 'active' : ''} onClick={() => setView('groups')}>Groups</button>
          </div>
        </div>

        {view === 'grid' ? (
          images.isLoading ? (
            <div className="spinner">Loading…</div>
          ) : items.length === 0 ? (
            <div className="empty">No photos match these filters.</div>
          ) : (
            <PhotoGrid
              items={items}
              hasNext={images.hasNextPage}
              isFetchingNext={images.isFetchingNextPage}
              fetchNext={images.fetchNextPage}
              onOpen={(i) => openImage(items[i].id)}
              onDecision={setDecision}
              people={people}
              onFaceChange={refetchPeople}
            />
          )
        ) : view === 'scenes' ? (
          <SceneView
            query={scenes}
            onOpen={openScene}
          />
        ) : (
          <GroupView
            query={groups}
            onOpen={openGroup}
            reviewOpen={nav?.kind === 'group'}
          />
        )}
      </div>

      {nav?.kind === 'lightbox' && gridLbIndex >= 0 && (
        <Lightbox
          items={items}
          index={gridLbIndex}
          setIndex={gridLbSetIndex}
          onDecision={setDecision}
        />
      )}

      {nav?.kind === 'group' && openGroupObj && (
        <GroupReview
          group={openGroupObj}
          selId={nav.imgId}
          zoom={!!nav.zoom}
          onSelect={selectImage}
          onZoom={setZoom}
          onClose={closeOverlay}
          onDecision={setDecision}
          onDecisionsBulk={setDecisionsBulk}
        />
      )}

      {nav?.kind === 'scene' && openSceneObj && (
        <ScenePanel
          scene={openSceneObj}
          selId={nav.imgId}
          zoom={!!nav.zoom}
          onSelect={selectImage}
          onZoom={setZoom}
          onClose={closeOverlay}
          onDecision={setDecision}
          onDecisionsBulk={setDecisionsBulk}
        />
      )}

      {showAnalyze && (
        <AnalyzePanel
          defaultFolder={meta.data?.meta?.folder || ''}
          onClose={() => setShowAnalyze(false)}
          onDone={refetchPeople}
        />
      )}

      {showSettings && (
        <SettingsPanel
          onClose={() => setShowSettings(false)}
          onChange={() => qc.invalidateQueries({ queryKey: ['meta'] })}
        />
      )}
    </div>
  )
}
