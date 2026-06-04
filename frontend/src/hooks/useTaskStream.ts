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
  reconnecting: boolean
}

export function useTaskStream(taskId: string | null, onEnd?: (task: TaskSnapshot) => void): TaskStreamState {
  const [task, setTask] = useState<TaskSnapshot | null>(null)
  const [lines, setLines] = useState<string[]>([])
  const [partial, setPartial] = useState('')
  const [progress, setProgress] = useState<TaskProgress | null>(null)
  const [connected, setConnected] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const endedRef = useRef<string | null>(null)
  const lastSeqRef = useRef(0)

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
    setReconnecting(false)
    endedRef.current = null
    lastSeqRef.current = 0
    if (!taskId) return

    let es: EventSource | null = null
    let stopped = false
    let retryTimer: number | null = null

    const noteSeq = (e: MessageEvent) => {
      const n = Number(e.lastEventId)
      if (Number.isFinite(n) && n > lastSeqRef.current) lastSeqRef.current = n
    }

    const connect = (attempt = 0) => {
      if (stopped) return
      es?.close()
      const suffix = lastSeqRef.current ? `?after=${lastSeqRef.current}` : ''
      es = new EventSource(taskStreamUrl(taskId) + suffix)
      es.onopen = () => {
        setConnected(true)
        setReconnecting(false)
      }
      es.addEventListener('snapshot', (e) => {
        setTask(JSON.parse((e as MessageEvent).data) as TaskSnapshot)
      })
      es.addEventListener('line', (e) => {
        noteSeq(e as MessageEvent)
        const ln = JSON.parse((e as MessageEvent).data) as string
        setLines((prev) => [...prev, ln])
      })
      es.addEventListener('partial', (e) => {
        noteSeq(e as MessageEvent)
        setPartial(JSON.parse((e as MessageEvent).data) as string)
      })
      es.addEventListener('progress', (e) => {
        noteSeq(e as MessageEvent)
        const p = JSON.parse((e as MessageEvent).data) as TaskProgress
        setProgress(p)
        setTask((prev) => prev ? {
          ...prev,
          phase: p.phase ?? prev.phase,
          progress: p.pct ?? prev.progress,
          message: p.message ?? prev.message,
        } : prev)
      })
      es.addEventListener('end', (e) => {
        noteSeq(e as MessageEvent)
        setPartial('')
        setConnected(false)
        setReconnecting(false)
        es?.close()
        void finish()
      })
      es.onerror = () => {
        setConnected(false)
        es?.close()
        fetchTask(taskId).then((latest) => {
          setTask(latest)
          if (latest.state === 'running' && !stopped) {
            setReconnecting(true)
            const delay = Math.min(5000, 500 * 2 ** attempt)
            retryTimer = window.setTimeout(() => connect(attempt + 1), delay)
          } else {
            setReconnecting(false)
            void finish()
          }
        }).catch(() => {
          if (!stopped) {
            setReconnecting(true)
            retryTimer = window.setTimeout(() => connect(attempt + 1),
              Math.min(5000, 500 * 2 ** attempt))
          }
        })
      }
    }

    connect()
    return () => {
      stopped = true
      if (retryTimer != null) window.clearTimeout(retryTimer)
      es?.close()
      setConnected(false)
      setReconnecting(false)
    }
  }, [taskId, finish])

  return { task, lines, partial, progress, connected, reconnecting }
}

