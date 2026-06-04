import { useState, useRef, useEffect, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { fetchTasks, startTask } from '../api'
import { hideDelContainers } from '../format'
import { useInfiniteScroll } from '../hooks/useInfiniteScroll'
import { useGridKeyboardNav } from '../hooks/useGridKeyboardNav'
import GroupPile from './GroupPile'
import TaskPanel from './TaskPanel'
import type { GroupsResponse, TaskSnapshot } from '../api/types'

// Minimal slice of the useInfiniteQuery result this view consumes (decoupled
// from react-query's generics; the real result is structurally assignable).
interface GroupViewQuery {
  data?: { pages: GroupsResponse[] }
  isLoading: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => unknown
}

interface GroupViewProps {
  query: GroupViewQuery
  onOpen: (dupGroup: number) => void
  reviewOpen?: boolean
  hideDel?: boolean
  onTaskDone?: (task: TaskSnapshot) => void
}

// Overview of duplicate groups as stacked photo piles. Arrow keys move a
// keyboard focus across piles; Enter / click asks the app to open the review
// overlay (`onOpen(dup_group)`). The overlay itself lives at the app root so
// it is URL-driven / Back-navigable. `reviewOpen` lets the app pause grid keys
// while that overlay is up (the overlay handles its own keyboard).
export default function GroupView({ query, onOpen, reviewOpen = false, hideDel = false, onTaskDone }: GroupViewProps) {
  const [culling, setCulling] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskError, setTaskError] = useState<string | null>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const qc = useQueryClient()

  useEffect(() => {
    fetchTasks().then((r) => {
      const cur = r.current
      if (cur?.state === 'running' && cur.type === 'autocull_duplicates') {
        setTaskId(cur.id)
        setCulling(true)
      }
    }).catch(() => {})
  }, [])

  const doAutocull = async () => {
    if (!window.confirm(
      'Across ALL duplicate groups, mark the best photo "keep" and the rest ' +
      '"delete"? This overwrites existing marks inside groups (files are not ' +
      'moved until you Apply).')) return
    setCulling(true)
    setTaskError(null)
    try {
      const task = await startTask('autocull_duplicates')
      setTaskId(task.id)
    } catch (e) {
      setTaskError(e instanceof Error ? e.message : 'Auto-cull failed.')
      setCulling(false)
    }
  }

  const taskDone = (task: TaskSnapshot) => {
    setCulling(false)
    setTaskError(null)
    qc.invalidateQueries({ queryKey: ['groups'] })
    qc.invalidateQueries({ queryKey: ['images'] })
    qc.invalidateQueries({ queryKey: ['applyStatus'] })
    onTaskDone?.(task)
  }

  const rawGroups = query.data?.pages.flatMap((p) => p.groups) ?? []
  const groups = hideDelContainers(rawGroups, hideDel ? 'notdel' : '')

  // Infinite scroll via an IntersectionObserver sentinel at the list end.
  useInfiniteScroll(sentinelRef, query)

  // Open the review overlay for a pile by index (Enter via the hook, click via
  // GroupPile). The click path also takes keyboard focus first.
  const activate = useCallback((idx: number) => {
    const g = groups[idx]
    if (g) onOpen(g.dup_group)
  }, [groups, onOpen])

  const { focusIdx, setFocusIdx, scrollRef, pileGridRef, onKeyDown } = useGridKeyboardNav({
    count: groups.length,
    onActivate: activate,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
    enabled: !reviewOpen,
  })

  const isFetchingNextPage = query.isFetchingNextPage

  if (query.isLoading) return <div className="spinner">Loading groups…</div>
  if (groups.length === 0) return <div className="empty">No duplicate groups found.</div>

  return (
    <div
      className="grid-scroll"
      ref={scrollRef}
      tabIndex={0}
      role="grid"
      aria-label="Duplicate groups — arrow keys to move, Enter to open, Esc to go back"
      onKeyDown={onKeyDown}
    >
      <div className="group-actionbar">
        <button className="btn primary" disabled={culling} onClick={doAutocull}>
          {culling ? 'Culling…' : 'Auto-cull all groups · keep best, delete rest'}
        </button>
        <span className="group-hint">Marks only — review or undo before applying.</span>
      </div>
      {taskError && <div className="af-error">{taskError}</div>}
      <TaskPanel taskId={taskId} title="Auto-cull task" compact onDone={taskDone} />
      <div className="pile-grid" ref={pileGridRef}>
        {groups.map((g, i) => (
          <GroupPile
            key={g.dup_group}
            group={g}
            focused={i === focusIdx}
            onOpen={() => { setFocusIdx(i); activate(i) }}
          />
        ))}
      </div>
      <div ref={sentinelRef} />
      {isFetchingNextPage && <div className="spinner">Loading more…</div>}
    </div>
  )
}
