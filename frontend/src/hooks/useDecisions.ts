import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { setDecision as apiSetDecision } from '../api'
import { invalidateRoots, PHOTO_LIST_QUERY_ROOTS, queryRootIn } from '../queryKeys'
import type { ImageItem } from '../api/types'
import type { BulkDecision, Decision } from '../types'

interface CachePage {
  items?: ImageItem[]
  groups?: { items: ImageItem[] }[]
  scenes?: { items: ImageItem[] }[]
}

interface CacheData {
  pages: CachePage[]
}

export function useDecisions() {
  const qc = useQueryClient()

  const invalidateLists = useCallback(() => {
    invalidateRoots(qc, PHOTO_LIST_QUERY_ROOTS)
  }, [qc])

  // Map every cached image (across the flat grid + grouped/scene member lists)
  // through `mutate`, leaving everything else untouched. One traversal definition
  // so optimistic decision *and* trash patches stay in lock-step.
  const patchCache = useCallback((mutate: (it: ImageItem) => ImageItem) => {
    qc.setQueriesData<CacheData>(
      { predicate: (q) => queryRootIn(q.queryKey, PHOTO_LIST_QUERY_ROOTS) },
      (data) => {
        if (!data?.pages) return data
        const patchItems = (arr?: ImageItem[]) => arr?.map(mutate)
        return {
          ...data,
          pages: data.pages.map((pg) => ({
            ...pg,
            items: patchItems(pg.items),
            groups: pg.groups?.map((g) => ({ ...g, items: patchItems(g.items) ?? [] })),
            scenes: pg.scenes?.map((s) => ({ ...s, items: patchItems(s.items) ?? [] })),
          })),
        }
      },
    )
  }, [qc])

  const patchDecision = useCallback((id: number, decision: string | null) => {
    patchCache((it) => (it.id === id ? { ...it, decision } : it))
  }, [patchCache])

  // Optimistically mark photos as trashed so they drop out of every list the
  // instant a trash task starts — no waiting for the task to finish and the
  // queries to refetch. The eventual invalidate reconciles against the server.
  const patchTrashed = useCallback((ids: number[]) => {
    const idset = new Set(ids)
    patchCache((it) => (idset.has(it.id) ? { ...it, trash_state: 'trashed' } : it))
  }, [patchCache])

  // Same, but for the "Trash all marked Del" flow where the caller doesn't carry
  // the id list: every del-marked photo currently in cache is the trash target.
  const patchTrashedDel = useCallback(() => {
    patchCache((it) => (it.decision === 'del' ? { ...it, trash_state: 'trashed' } : it))
  }, [patchCache])

  const setDecision = useCallback(async (item: ImageItem, decision: Decision) => {
    const next = item.decision === decision ? null : decision
    patchDecision(item.id, next)
    if (item.hash == null) return
    try {
      await apiSetDecision(item.hash, next)
    } catch {
      invalidateLists()
    }
  }, [patchDecision, invalidateLists])

  const setDecisionsBulk = useCallback(async (updates: BulkDecision[]) => {
    updates.forEach((u) => patchDecision(u.id, u.decision))
    try {
      await Promise.all(updates
        .filter((u): u is BulkDecision & { hash: string } => u.hash != null)
        .map((u) => apiSetDecision(u.hash, u.decision)))
    } catch {
      invalidateLists()
    }
  }, [patchDecision, invalidateLists])

  return { invalidateLists, patchDecision, patchTrashed, patchTrashedDel, setDecision, setDecisionsBulk }
}
