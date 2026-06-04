import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchTask, taskStreamUrl } from '../api'
import type { TaskSnapshot } from '../api/types'

export interface TaskProgress {
  phase?: string | null
  pct?: number | null
  message?: string | null
  current?: number | null
  total?: number | null
}

export interface TaskStreamState {
  task: TaskSnapshot | null
  lines: string[]
  partial: string
  progress: TaskProgress | null
  connected: boolean
}

export function useTaskStream(taskId: string | null, onEnd?: (task: TaskSnapshot) => void): TaskStreamState {
  const [task, setTask] = useState<TaskSnapshot | null>(null)
  const [lines, setLines] = useState<string[]>([])
  const [partial, setPartial] = useState('')
  const [progress, setProgress] = useState<TaskProgress | null>(null)
  const [connected, setConnected] = useState(false)
  const endedRef = useRef<string | null>(null)

  const finish = useCallback(async () => {
    if (!taskId || endedRef.current === taskId) return
    try {
      const latest = await fetchTask(taskId)
      setTask(latest)
      if (latest.state !== 'running') {
        endedRef.current = taskId
        onEnd?.(latest)
      }
    } catch {
      // If the final fetch fails, the stream has still ended; leave the last
      // snapshot visible instead of erasing useful logs.
    }
  }, [taskId, onEnd])

  useEffect(() => {
    setTask(null)
    setLines([])
    setPartial('')
    setProgress(null)
    setConnected(false)
    endedRef.current = null
    if (!taskId) return

    const es = new EventSource(taskStreamUrl(taskId))
    setConnected(true)
    es.addEventListener('snapshot', (e) => {
      setTask(JSON.parse((e as MessageEvent).data) as TaskSnapshot)
    })
    es.addEventListener('line', (e) => {
      const ln = JSON.parse((e as MessageEvent).data) as string
      setLines((prev) => [...prev, ln])
    })
    es.addEventListener('partial', (e) => {
      setPartial(JSON.parse((e as MessageEvent).data) as string)
    })
    es.addEventListener('progress', (e) => {
      const p = JSON.parse((e as MessageEvent).data) as TaskProgress
      setProgress(p)
      setTask((prev) => prev ? {
        ...prev,
        phase: p.phase ?? prev.phase,
        progress: p.pct ?? prev.progress,
        message: p.message ?? prev.message,
      } : prev)
    })
    es.addEventListener('end', () => {
      setPartial('')
      setConnected(false)
      es.close()
      void finish()
    })
    es.onerror = () => {
      setConnected(false)
      es.close()
      void finish()
    }
    return () => {
      es.close()
      setConnected(false)
    }
  }, [taskId, finish])

  return { task, lines, partial, progress, connected }
}

