import { useState, useCallback, useEffect } from 'react'
import { useQuery, useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { fetchMeta, fetchImages, fetchGroups, setDecision as apiSetDecision } from './api.js'
import { DEFAULT_FILTERS, parseState, buildSearch } from './urlState.js'
import Sidebar from './components/Sidebar.jsx'
import PhotoGrid from './components/PhotoGrid.jsx'
import GroupView from './components/GroupView.jsx'
import Lightbox from './components/Lightbox.jsx'

const PAGE = 60
const GROUP_PAGE = 30

// Hydrate initial state from the URL so reloads/shared links restore the view.
const INITIAL = parseState(window.location.search)

export default function App() {
  const [filters, setFilters] = useState(INITIAL.filters)
  const [view, setView] = useState(INITIAL.view)   // 'grid' | 'groups'
  const [lightboxIdx, setLightboxIdx] = useState(null)
  const qc = useQueryClient()

  // Mirror filter + view state into the URL (replace, so we don't spam history).
  useEffect(() => {
    const search = buildSearch(filters, view)
    const next = window.location.pathname + search
    if (next !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', next)
    }
  }, [filters, view])

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

  const items = images.data?.pages.flatMap((p) => p.items) ?? []
  const total = images.data?.pages[0]?.total ?? 0
  const groupTotal = groups.data?.pages[0]?.total ?? 0

  const updateFilter = useCallback((patch) => {
    setFilters((f) => ({ ...f, ...patch }))
  }, [])

  const resetFilters = useCallback(() => setFilters(DEFAULT_FILTERS), [])

  // After a face/person edit, refetch everything that renders names or counts.
  const refetchPeople = useCallback(() => {
    qc.invalidateQueries({
      predicate: (q) => ['images', 'groups', 'meta'].includes(q.queryKey[0]),
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
      { predicate: (q) => ['images', 'groups'].includes(q.queryKey[0]) },
      (data) => {
        if (!data?.pages) return data
        return {
          ...data,
          pages: data.pages.map((pg) => ({
            ...pg,
            items: pg.items?.map((it) => (it.id === id ? { ...it, decision } : it)),
            groups: pg.groups?.map((g) => ({
              ...g,
              items: g.items.map((it) => (it.id === id ? { ...it, decision } : it)),
            })),
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
      qc.invalidateQueries({ predicate: (q) => ['images', 'groups'].includes(q.queryKey[0]) })
    }
  }, [patchDecision, qc])

  // Bulk apply (e.g. "keep best, delete rest"). updates: [{id, hash, decision}]
  const setDecisionsBulk = useCallback(async (updates) => {
    updates.forEach((u) => patchDecision(u.id, u.decision))
    try {
      await Promise.all(updates.map((u) => apiSetDecision(u.hash, u.decision)))
    } catch {
      qc.invalidateQueries({ predicate: (q) => ['images', 'groups'].includes(q.queryKey[0]) })
    }
  }, [patchDecision, qc])

  const headerCount = view === 'grid' ? total : groupTotal
  const headerLabel = view === 'grid' ? 'photos' : 'duplicate groups'

  return (
    <div className="app">
      <Sidebar
        meta={meta.data}
        filters={filters}
        updateFilter={updateFilter}
        toggleInList={toggleInList}
        resetFilters={resetFilters}
        total={total}
        view={view}
        onPeopleChange={refetchPeople}
      />
      <div className="main">
        <div className="topbar">
          {view === 'grid' && (
            <input
              className="search"
              type="text"
              placeholder="Search captions…"
              key={filters.q}
              defaultValue={filters.q}
              onKeyDown={(e) => { if (e.key === 'Enter') updateFilter({ q: e.target.value }) }}
              onBlur={(e) => { if (e.target.value !== filters.q) updateFilter({ q: e.target.value }) }}
            />
          )}
          <span className="result-count">
            {headerCount.toLocaleString()} {headerLabel}
          </span>
          <div className="spacer" />
          <div className="seg view-toggle">
            <button className={view === 'grid' ? 'active' : ''} onClick={() => setView('grid')}>Grid</button>
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
              onOpen={setLightboxIdx}
              onDecision={setDecision}
              people={meta.data?.clusters ?? []}
              onFaceChange={refetchPeople}
            />
          )
        ) : (
          <GroupView
            query={groups}
            onDecision={setDecision}
            onDecisionsBulk={setDecisionsBulk}
            people={meta.data?.clusters ?? []}
          />
        )}
      </div>

      {lightboxIdx != null && items[lightboxIdx] && (
        <Lightbox
          items={items}
          index={lightboxIdx}
          setIndex={setLightboxIdx}
          onDecision={setDecision}
        />
      )}
    </div>
  )
}
