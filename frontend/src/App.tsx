import { useState, useCallback, useEffect, useRef } from 'react'
import { useQuery, useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { fetchMeta, fetchImages, fetchGroups, fetchScenes, fetchPeople, startTask, fetchTask, regroupScenes, mergeScenes, splitScene, unmergeScene } from './api'
import { DEFAULT_FILTERS, parseState } from './urlState'
import { applyTrashHide, groupActivity } from './format'
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
import PeopleView from './components/PeopleView'
import PersonPage from './components/PersonPage'
import Toasts from './components/Toasts'
import Dialogs from './components/Dialogs'
import { isDialogOpen } from './dialog'
import ShortcutHelp from './components/ShortcutHelp'
import { toast } from './toast'
import { isTypingTarget } from './keys'

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
  const [view, setView] = useState<View>(INITIAL.view)   // 'grid' | 'groups' | 'scenes' | 'people'
  const [showAnalyze, setShowAnalyze] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [tileSize, setTileSize] = useState(() => {
    const saved = Number(localStorage.getItem('sift.grid.tileSize'))
    return [210, 285, 370].includes(saved) ? saved : 285
  })
  const qc = useQueryClient()
  const searchRef = useRef<HTMLInputElement>(null)
  const { setDecision, undoLastMark, patchTrashed, saveReview, reviewBusy,
    reviewMessage, canUndoReview, undoReview } = useDecisions()
  const { activeTask, invalidateAfterTask } = useTaskInvalidation()

  const focusGrid = useCallback(() => {
    document.querySelector<HTMLElement>('.grid-scroll')?.focus()
  }, [])

  // "What overlay is open" + all of its browser-history choreography lives in
  // useOverlayNav; here we just consume the resulting nav state and actions.
  const {
    nav, navigate, closeOverlay,
    openImage, openGroup, openScene, openPerson, selectImage, setZoom,
  } = useOverlayNav({ filters, view, initial: INITIAL, setFilters, setView, focusGrid })

  // Global shortcut: "/" jumps to the search box (unless already typing or a
  // modal is open). Pairs with the grid's arrow-key navigation.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.key !== '/' && e.key !== '?') || e.ctrlKey || e.metaKey || e.altKey) return
      if (isTypingTarget(e.target)) return
      if (nav != null || showAnalyze || showSettings || showHelp) return
      e.preventDefault()
      // "?" opens the keyboard reference; "/" jumps to search.
      if (e.key === '?') { setShowHelp(true); return }
      searchRef.current?.focus()
      searchRef.current?.select()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nav, showAnalyze, showSettings, showHelp])

  // Ctrl/Cmd+Z takes back the last keep / Trash mark, wherever it was made
  // (grid, viewer or a review overlay). Text fields keep their own undo.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== 'z') return
      if (isTypingTarget(e.target) || isDialogOpen()) return
      e.preventDefault()
      void undoLastMark()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undoLastMark])

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
    queryFn: ({ pageParam }) => fetchGroups(filters, pageParam, GROUP_PAGE, 'review'),
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

  // People view: everyone found in the library. A person's own page is an
  // overlay-style nav entry (per=), so Back returns to the people list.
  const openPersonId = view === 'people' && nav?.kind === 'person' ? nav.refId ?? null : null
  const persons = useQuery({
    queryKey: ['people'],
    queryFn: fetchPeople,
    enabled: view === 'people',
  })
  const personList = persons.data?.people ?? []

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
  const groupInPage = nav?.kind === 'group'
    ? (groups.data?.pages.flatMap((p) => p.groups) ?? []).find((g) => g.dup_group === nav.refId) ?? null
    : null
  const openGroupId = nav?.kind === 'group' ? nav.refId : null
  const groupDetail = useQuery({
    queryKey: ['groups', 'detail', openGroupId, filters],
    queryFn: async () => ({ pages: [await fetchGroups(filters, 0, 1, 'id', openGroupId!)] }),
    enabled: openGroupId != null && groupInPage == null,
  })
  const openGroupObj = groupInPage ?? groupDetail.data?.pages[0]?.groups[0] ?? null
  const loadedGroups = groups.data?.pages.flatMap((page) => page.groups) ?? []
  const nextGroup = (catalog: typeof loadedGroups) => {
    const current = catalog.findIndex((group) => group.dup_group === openGroupId)
    const rest = catalog.slice(current + 1)
    return rest.find((group) => groupActivity(group.items).needsReview)
      ?? catalog.slice(0, Math.max(0, current)).find((group) => groupActivity(group.items).needsReview)
      ?? rest[0]
  }
  const openNextGroup = async () => {
    let next = nextGroup(loadedGroups)
    if (!next && groups.hasNextPage) {
      const result = await groups.fetchNextPage()
      next = nextGroup(result.data?.pages.flatMap((page) => page.groups) ?? [])
    }
    if (next) openGroup(next.dup_group)
  }
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
    try {
      const started = await startTask('trash_decisions', { image_ids: ids })
      let snap = started
      for (let i = 0; i < 150 && snap.state === 'running'; i++) {
        await new Promise((r) => setTimeout(r, 400))
        snap = await fetchTask(started.id)
      }
      invalidateAfterTask(snap)
      if (snap.state === 'failed') throw new Error(snap.error || 'Moving photos to Trash failed.')
    } catch (error) {
      // The photos were hidden optimistically; bring back whatever is still on
      // disk rather than leaving the list claiming they were trashed.
      invalidateRoots(qc, PHOTO_DATA_QUERY_ROOTS)
      throw error
    }
  }, [invalidateAfterTask, patchTrashed, qc])

  // Scene granularity knob: re-segment scenes by capture-time gap, then refetch
  // scenes (new grouping) and meta (new scene count + remembered gap).
  const onRegroupScenes = useCallback(async (gap: number) => {
    try {
      await regroupScenes(gap)
    } catch (error) {
      toast.failure('Couldn’t regroup scenes.', error)
    }
    qc.invalidateQueries({ queryKey: ['scenes'] })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }, [qc])

  // Manual scene merge / unmerge: pin (or release) scenes, then refetch the
  // re-segmented scenes and the scene count.
  const onMergeScenes = useCallback(async (sceneGroups: number[]) => {
    try {
      await mergeScenes(sceneGroups)
    } catch (error) {
      toast.failure('Couldn’t merge those scenes.', error)
      throw error
    }
    qc.invalidateQueries({ queryKey: ['scenes'] })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }, [qc])

  const onUnmergeScene = useCallback(async (sceneGroup: number) => {
    try {
      await unmergeScene(sceneGroup)
    } catch (error) {
      toast.failure('Couldn’t unmerge that scene.', error)
      throw error
    }
    qc.invalidateQueries({ queryKey: ['scenes'] })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }, [qc])

  const onSplitScene = useCallback(async (sceneGroup: number, imageId: number, hash: string) => {
    await splitScene(sceneGroup, imageId, hash)
    closeOverlay()
    invalidateRoots(qc, PHOTO_DATA_QUERY_ROOTS)
  }, [closeOverlay, qc])

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

  // Leaving the People view also leaves any open person page.
  const switchView = useCallback((next: View) => {
    if (nav?.kind === 'person') navigate(null, false)
    setView(next)
  }, [nav, navigate])

  // "Show photos" on a person's page: the photo grid, filtered to that person.
  const showPersonPhotos = useCallback((clusterId: number) => {
    navigate(null)
    setFilters({ ...DEFAULT_FILTERS, people: [String(clusterId)] })
    setView('grid')
  }, [navigate])

  const peopleTotal = personList.filter((p) => p.cluster_id >= 0).length
  const headerCount = view === 'grid' ? total : view === 'scenes' ? sceneTotal
    : view === 'people' ? peopleTotal : groupTotal
  const headerLabel = view === 'grid' ? 'photos'
    : view === 'scenes' ? 'scenes' : view === 'people' ? 'people' : 'duplicate groups'
  const activeTaskPct = activeTask?.progress == null
    ? null : Math.round(Math.max(0, Math.min(1, activeTask.progress)) * 100)
  const activeTaskLabel = activeTask ? (TASK_LABELS[activeTask.type] ?? activeTask.type) : null
  const currentQuery = view === 'grid' ? images : view === 'groups' ? groups
    : view === 'people' ? persons : scenes
  const reviewActions = {
    onSaveReview: saveReview, onUndoReview: canUndoReview ? undoReview : undefined,
    reviewBusy, reviewMessage,
  }

  return (
    <div className="app sift-review-ux">
      <Sidebar
        meta={meta.data}
        filters={filters}
        updateFilter={updateFilter}
        toggleInList={toggleInList}
        resetFilters={resetFilters}
        shownCount={headerCount}
        shownLabel={headerLabel}
        onPeopleChange={refetchPeople}
        onTaskDone={invalidateAfterTask}
        onOpenTrash={() => {
          setView('grid')
          updateFilter({ trash: 'trashed', decision: 'all' })
        }}
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
            {currentQuery.isError ? 'Connection problem' : `${headerCount.toLocaleString()} ${headerLabel}`}
          </span>
          {view === 'grid' && <label className="grid-size">
            <span>Thumbnails</span>
            <select aria-label="Thumbnail size" value={tileSize} onChange={(event) => {
              const size = Number(event.currentTarget.value)
              setTileSize(size)
              localStorage.setItem('sift.grid.tileSize', String(size))
            }}>
              <option value={210}>Small</option><option value={285}>Medium</option><option value={370}>Large</option>
            </select>
          </label>}
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
          <button className="btn help-btn" onClick={() => setShowHelp(true)}
            aria-label="Keyboard shortcuts" title="Keyboard shortcuts (?)">
            ?
          </button>
          <div className="seg view-toggle">
            <button className={view === 'grid' ? 'active' : ''} onClick={() => switchView('grid')}>Grid</button>
            <button className={view === 'scenes' ? 'active' : ''} onClick={() => switchView('scenes')}>Scenes</button>
            <button className={view === 'groups' ? 'active' : ''} onClick={() => switchView('groups')}>Groups</button>
            <button className={view === 'people' ? 'active' : ''} onClick={() => switchView('people')}>People</button>
          </div>
        </div>
        {nav == null && (reviewMessage || canUndoReview) && (
          <div className="review-notice" role="status">
            <span>{reviewMessage}</span>
            {canUndoReview && <button className="btn" disabled={reviewBusy} onClick={undoReview}>Undo marks</button>}
          </div>
        )}

        {currentQuery.isError ? (
          <div className="empty" role="alert">
            <p>Couldn’t load {headerLabel}. Check that the Sift server is running.</p>
            <button className="btn primary" disabled={currentQuery.isFetching}
              onClick={() => void currentQuery.refetch()}>{currentQuery.isFetching ? 'Retrying…' : 'Retry'}</button>
          </div>
        ) : view === 'grid' ? (
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
              <div className="empty">
                <p>No photos match these filters.</p>
                <button className="btn" onClick={resetFilters}>Reset filters</button>
              </div>
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
              tileSize={tileSize}
            />
          )
        ) : view === 'people' ? (
          openPersonId != null ? (
            <PersonPage
              key={openPersonId}
              clusterId={openPersonId}
              people={personList}
              onBack={closeOverlay}
              onOpenPerson={openPerson}
              onShowPhotos={showPersonPhotos}
              onChanged={refetchPeople}
            />
          ) : (
            <PeopleView people={personList} loading={persons.isLoading} onOpen={openPerson} />
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
            showTrashed={filters.trash.includes('trashed') || filters.trash.includes('any')}
          />
        ) : (
          <GroupView
            query={groups}
            onOpen={openGroup}
            reviewOpen={nav?.kind === 'group'}
            onSaveReview={saveReview}
            reviewBusy={reviewBusy}
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
          key={openGroupObj.dup_group}
          group={openGroupObj}
          selId={nav.imgId}
          zoom={!!nav.zoom}
          onSelect={selectImage}
          onZoom={setZoom}
          onClose={closeOverlay}
          onDecision={setDecision}
          {...reviewActions}
          onNextGroup={nextGroup(loadedGroups) || groups.hasNextPage ? () => void openNextGroup() : undefined}
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
          {...reviewActions}
          onApplyDeletes={applyDeletes}
          onSplitScene={onSplitScene}
          onOpenGroup={openGroup}
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
          onClose={() => setShowAnalyze(false)}
          onDone={invalidateAfterTask}
        />
      )}

      {showHelp && <ShortcutHelp onClose={() => setShowHelp(false)} />}
      <Dialogs />
      <Toasts />

      {showSettings && (
        <SettingsPanel
          onClose={() => setShowSettings(false)}
          // Broad refetch: roots edits move counts, and a decisions import moves
          // the keep/del badges across the grid/groups/scenes.
          onChange={() => invalidateRoots(qc, PHOTO_DATA_QUERY_ROOTS)}
        />
      )}
    </div>
  )
}
