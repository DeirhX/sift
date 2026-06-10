import { useState, useCallback, useEffect, useRef } from 'react'
import { useQuery, useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { fetchMeta, fetchImages, fetchGroups, fetchScenes, startTask, fetchTask, regroupScenes, mergeScenes, unmergeScene } from './api'
import { DEFAULT_FILTERS, parseState } from './urlState'
import { applyTrashHide } from './format'
import { useOverlayNav } from './hooks/useOverlayNav'
import { useDecisions } from './hooks/useDecisions'
import { useTaskInvalidation } from './hooks/useTaskInvalidation'
import { invalidateRoots, PHOTO_DATA_QUERY_ROOTS } from './queryKeys'
import type { Filters, View } from './urlState'
import type { SetLightboxIndex } from './types'
import Sidebar from './components/Sidebar'
import PhotoGrid from './components/PhotoGrid'
import GroupView from './components/GroupView'
import SceneView from './components/SceneView'
import GroupReview from './components/GroupReview'
import ScenePanel from './components/ScenePanel'
import Lightbox from './components/Lightbox'
import AnalyzePanel from './components/AnalyzePanel'
import SettingsPanel from './components/SettingsPanel'
import SceneGranularity from './components/SceneGranularity'

const PAGE = 60
const GROUP_PAGE = 30
const SCENE_PAGE = 30
const TASK_LABELS: Record<string, string> = {
  analyze_library: 'Analyze',
  index_library: 'Index',
  apply_decisions: 'Trash',
  trash_decisions: 'Trash',
  undo_apply: 'Restore',
  restore_trash: 'Restore',
  empty_trash: 'Empty Trash',
  autocull_duplicates: 'Auto-cull',
}

// Hydrate initial state from the URL so reloads/shared links restore the view.
const INITIAL = parseState(window.location.search)

export default function App() {
  const [filters, setFilters] = useState<Filters>(INITIAL.filters)
  const [view, setView] = useState<View>(INITIAL.view)   // 'grid' | 'groups' | 'scenes'
  const [showAnalyze, setShowAnalyze] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const qc = useQueryClient()
  const searchRef = useRef<HTMLInputElement>(null)
  const { setDecision, setDecisionsBulk, patchTrashed } = useDecisions()
  const { activeTask, invalidateAfterTask } = useTaskInvalidation()

  const focusGrid = useCallback(() => {
    document.querySelector<HTMLElement>('.grid-scroll')?.focus()
  }, [])

  // "What overlay is open" + all of its browser-history choreography lives in
  // useOverlayNav; here we just consume the resulting nav state and actions.
  const {
    nav, closeOverlay,
    openImage, openGroup, openScene, selectImage, setZoom,
  } = useOverlayNav({ filters, view, initial: INITIAL, setFilters, setView, focusGrid })

  // Global shortcut: "/" jumps to the search box (unless already typing or a
  // modal is open). Pairs with the grid's arrow-key navigation.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return
      if (/^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement).tagName)) return
      if (nav != null || showAnalyze || showSettings) return
      e.preventDefault()
      searchRef.current?.focus()
      searchRef.current?.select()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nav, showAnalyze, showSettings])

  const meta = useQuery({ queryKey: ['meta'], queryFn: fetchMeta })

  const images = useInfiniteQuery({
    queryKey: ['images', filters],
    queryFn: ({ pageParam }) => fetchImages(filters, pageParam, PAGE),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.limit
      return next < last.total ? next : undefined
    },
    enabled: view === 'grid',
  })

  const groups = useInfiniteQuery({
    queryKey: ['groups', filters],
    queryFn: ({ pageParam }) => fetchGroups(filters, pageParam, GROUP_PAGE),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.limit
      return next < last.total ? next : undefined
    },
    enabled: view === 'groups',
  })

  const scenes = useInfiniteQuery({
    queryKey: ['scenes', filters],
    queryFn: ({ pageParam }) => fetchScenes(filters, pageParam, SCENE_PAGE),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.limit
      return next < last.total ? next : undefined
    },
    enabled: view === 'scenes',
  })

  // Grid drops trashed photos (optimistically patched the instant a trash starts)
  // at render time — except on the Trash filter (trash='trashed'), which exists to
  // show them. The Decision axis is handled server-side; live-culling of del marks
  // happens inside the review overlay, not the grid.
  const items = applyTrashHide(
    images.data?.pages.flatMap((p) => p.items) ?? [], filters.trash === 'trashed')
  const total = images.data?.pages[0]?.total ?? 0
  const groupTotal = groups.data?.pages[0]?.total ?? 0
  const sceneTotal = scenes.data?.pages[0]?.total ?? 0

  const people = meta.data?.clusters ?? []

  // Resolve the open overlay's backing record from the loaded query pages. May
  // be null right after a deep-link/reload until the relevant page arrives;
  // the overlay simply waits rather than rendering against missing data. The
  // review itself live-hides culled/trashed members (with reveal toggles).
  const openGroupObj = nav?.kind === 'group'
    ? (groups.data?.pages.flatMap((p) => p.groups) ?? []).find((g) => g.dup_group === nav.refId) ?? null
    : null
  const openSceneObj = nav?.kind === 'scene'
    ? (scenes.data?.pages.flatMap((p) => p.scenes) ?? []).find((s) => s.scene_group === nav.refId) ?? null
    : null

  // Adapt the index-based Lightbox (grid) to id-based nav: each move pushes a
  // history step; closing unwinds the overlay.
  const gridLbIndex = nav?.kind === 'lightbox' ? items.findIndex((it) => it.id === nav.imgId) : -1
  const gridLbSetIndex = useCallback<SetLightboxIndex>((v) => {
    const cur = items.findIndex((it) => it.id === nav?.imgId)
    const n = typeof v === 'function' ? v(cur) : v
    if (n == null) { closeOverlay(); return }
    if (n < 0 || n >= items.length || n === cur) return
    openImage(items[n].id)
  }, [items, nav, closeOverlay, openImage])

  const updateFilter = useCallback((patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }))
  }, [])

  const resetFilters = useCallback(() => setFilters(DEFAULT_FILTERS), [])

  // Immediate, scoped Trash from inside a scene: move just the named (del-marked)
  // photos to Trash. The optimistic patchTrashed drops them from the filmstrip
  // (and every other list) right away; we then poll the task to completion —
  // rather than trusting the 1s task poll, since a few-file trash can finish
  // between polls — and invalidate to reconcile against the server.
  const applyDeletes = useCallback(async (ids: number[]) => {
    if (!ids.length) return
    patchTrashed(ids)
    const started = await startTask('trash_decisions', { image_ids: ids })
    let snap = started
    for (let i = 0; i < 150 && snap.state === 'running'; i++) {
      await new Promise((r) => setTimeout(r, 400))
      snap = await fetchTask(started.id)
    }
    invalidateAfterTask(snap)
  }, [invalidateAfterTask, patchTrashed])

  // Scene granularity knob: re-segment scenes by capture-time gap, then refetch
  // scenes (new grouping) and meta (new scene count + remembered gap).
  const onRegroupScenes = useCallback(async (gap: number) => {
    await regroupScenes(gap)
    qc.invalidateQueries({ queryKey: ['scenes'] })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }, [qc])

  // Manual scene merge / unmerge: pin (or release) scenes, then refetch the
  // re-segmented scenes and the scene count.
  const onMergeScenes = useCallback(async (sceneGroups: number[]) => {
    await mergeScenes(sceneGroups)
    qc.invalidateQueries({ queryKey: ['scenes'] })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }, [qc])

  const onUnmergeScene = useCallback(async (sceneGroup: number) => {
    await unmergeScene(sceneGroup)
    qc.invalidateQueries({ queryKey: ['scenes'] })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }, [qc])

  // After a face/person edit, refetch everything that renders names or counts.
  const refetchPeople = useCallback(() => {
    invalidateRoots(qc, PHOTO_DATA_QUERY_ROOTS)
  }, [qc])

  const toggleInList = useCallback((key: 'tags' | 'people', value: string) => {
    setFilters((f) => {
      const cur = f[key]
      const nextList = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value]
      return { ...f, [key]: nextList } as Filters
    })
  }, [])

  const headerCount = view === 'grid' ? total : view === 'scenes' ? sceneTotal : groupTotal
  const headerLabel = view === 'grid' ? 'photos'
    : view === 'scenes' ? 'scenes' : 'duplicate groups'
  const activeTaskPct = activeTask?.progress == null
    ? null : Math.round(Math.max(0, Math.min(1, activeTask.progress)) * 100)
  const activeTaskLabel = activeTask ? (TASK_LABELS[activeTask.type] ?? activeTask.type) : null

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
        onTaskDone={invalidateAfterTask}
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
                if (e.key === 'Enter') { updateFilter({ q: e.currentTarget.value }); focusGrid() }
                else if (e.key === 'Escape') { e.currentTarget.blur(); focusGrid() }
              }}
              onBlur={(e) => { if (e.currentTarget.value !== filters.q) updateFilter({ q: e.currentTarget.value }) }}
            />
          )}
          <span className="result-count">
            {headerCount.toLocaleString()} {headerLabel}
          </span>
          {view === 'scenes' && (
            <SceneGranularity
              gap={Number(meta.data?.meta?.scene_gap) || 120}
              sceneCount={sceneTotal}
              onCommit={onRegroupScenes}
            />
          )}
          <div className="spacer" />
          {activeTask && (
            <button
              className="task-chip"
              onClick={() => setShowAnalyze(true)}
              title="Open the current task"
            >
              <span className="task-chip-dot" />
              <span>{activeTaskLabel}</span>
              {activeTaskPct != null && <span>{activeTaskPct}%</span>}
            </button>
          )}
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
            !meta.data?.meta?.folder ? (
              <div className="empty empty-cold">
                <p>No library yet — nothing has been analyzed.</p>
                <button className="btn primary" onClick={() => setShowAnalyze(true)}>
                  Analyze your first folder
                </button>
              </div>
            ) : (
              <div className="empty">No photos match these filters.</div>
            )
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
            reviewOpen={nav?.kind === 'scene'}
            activeTags={filters.tags}
            onToggleTag={(t) => toggleInList('tags', t)}
            onMerge={onMergeScenes}
            onUnmerge={onUnmergeScene}
          />
        ) : (
          <GroupView
            query={groups}
            onOpen={openGroup}
            reviewOpen={nav?.kind === 'group'}
            onTaskDone={invalidateAfterTask}
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
          defaultShowDeleted={filters.trash === 'trashed'}
          defaultShowCulled={filters.decision === 'del'}
        />
      )}

      {nav?.kind === 'scene' && openSceneObj && (
        <ScenePanel
          scene={openSceneObj}
          selId={nav.imgId ?? null}
          zoom={!!nav.zoom}
          onSelect={selectImage}
          onZoom={setZoom}
          onClose={closeOverlay}
          onDecision={setDecision}
          onDecisionsBulk={setDecisionsBulk}
          onApplyDeletes={applyDeletes}
          // The scene's member count tracks the active filters, so the panel opens
          // showing every member that passes them — del-marked photos included
          // (they're still in the library until trashed), so it agrees with the
          // pile's count. "Hide culled" re-enables cull-as-you-go on demand;
          // trashed photos surface only when the trash filter asks for them.
          defaultShowDeleted={filters.trash.includes('trashed') || filters.trash.includes('any')}
          defaultShowCulled
        />
      )}

      {showAnalyze && (
        <AnalyzePanel
          defaultFolder={meta.data?.meta?.folder || ''}
          onClose={() => setShowAnalyze(false)}
          onDone={invalidateAfterTask}
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
