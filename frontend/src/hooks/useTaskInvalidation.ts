import { useCallback, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchTasks } from '../api'
import {
  AUTOCULL_QUERY_ROOTS,
  invalidateRoots,
  PHOTO_LIST_QUERY_ROOTS,
  TASK_DATA_QUERY_ROOTS,
} from '../queryKeys'
import type { TaskSnapshot } from '../api/types'

export function useTaskInvalidation() {
  const qc = useQueryClient()

  const invalidateAfterTask = useCallback((task: TaskSnapshot) => {
    switch (task.type) {
      case 'analyze_library':
      case 'index_library':
      case 'apply_decisions':
      case 'trash_decisions':
      case 'undo_apply':
      case 'restore_trash':
      case 'empty_trash':
        invalidateRoots(qc, TASK_DATA_QUERY_ROOTS)
        break
      case 'autocull_duplicates':
        invalidateRoots(qc, AUTOCULL_QUERY_ROOTS)
        break
      default:
        invalidateRoots(qc, PHOTO_LIST_QUERY_ROOTS)
    }
  }, [qc])

  const taskList = useQuery({
    queryKey: ['tasks'],
    queryFn: () => fetchTasks(5),
    refetchInterval: 1000,
  })
  const activeTask = taskList.data?.current ?? null
  const lastRunningTaskId = useRef<string | null>(null)
  const completedTaskIds = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (activeTask?.state === 'running') {
      lastRunningTaskId.current = activeTask.id
      return
    }
    const latest = taskList.data?.tasks?.[0]
    if (!latest || latest.state === 'running') return
    if (latest.id !== lastRunningTaskId.current) return
    if (completedTaskIds.current.has(latest.id)) return
    completedTaskIds.current.add(latest.id)
    lastRunningTaskId.current = null
    invalidateAfterTask(latest)
  }, [activeTask, taskList.data?.tasks, invalidateAfterTask])

  return { activeTask, invalidateAfterTask }
}
