import { useState, useEffect, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { fetchTasks, startTask } from '../api'
import GroupPile from './GroupPile'
import TaskPanel from './TaskPanel'
import WindowedPileGrid from './WindowedPileGrid'
import type { GroupsResponse, TaskSnapshot } from '../api/types'

// Reserved height below each square stack: one quality-pill / decision-count
// row. Keeps pile cells uniform so the grid can window by row.
const GROUP_META_H = 44

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
  onTaskDone?: (task: TaskSnapshot) => void
}

// Overview of duplicate groups as stacked photo piles, rendered through the
// windowed pile grid so only on-screen piles mount. Arrow keys move a keyboard
// focus; Enter / click opens the review overlay (`onOpen(dup_group)`), which
// lives at the app root so it is URL-driven / Back-navigable. `reviewOpen` lets
// the app pause grid keys while that overlay is up.
export default function GroupView({ query, onOpen, reviewOpen = false, onTaskDone }: GroupViewProps) {
  const [culling, setCulling] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskError, setTaskError] = useState<string | null>(null)
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

  const groups = query.data?.pages.flatMap((p) => p.groups) ?? []

  const activate = useCallback((idx: number) => {
    const g = groups[idx]
    if (g) onOpen(g.dup_group)
  }, [groups, onOpen])

  const header = (
    <>
      <div className="group-actionbar">
        <button className="btn primary" disabled={culling} onClick={doAutocull}>
          {culling ? 'Culling…' : 'Auto-cull all groups · keep best, delete rest'}
        </button>
        <span className="group-hint">Marks only — review or undo before applying.</span>
      </div>
      {taskError && <div className="af-error">{taskError}</div>}
      <TaskPanel taskId={taskId} title="Auto-cull task" compact onDone={taskDone} />
    </>
  )

  return (
    <WindowedPileGrid
      items={groups}
      getKey={(g) => g.dup_group}
      metaHeight={GROUP_META_H}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      fetchNextPage={query.fetchNextPage}
      onActivate={activate}
      enabled={!reviewOpen}
      header={header}
      ariaLabel="Duplicate groups — arrow keys to move, Enter to open, Esc to go back"
      emptyLabel="No duplicate groups found."
      loading={query.isLoading}
      loadingLabel="Loading groups…"
      renderCell={(g, i, focused) => (
        <GroupPile
          group={g}
          focused={focused}
          onOpen={() => activate(i)}
        />
      )}
    />
  )
}
