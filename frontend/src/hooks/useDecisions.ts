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

  const patchDecision = useCallback((id: number, decision: string | null) => {
    qc.setQueriesData<CacheData>(
      { predicate: (q) => queryRootIn(q.queryKey, PHOTO_LIST_QUERY_ROOTS) },
      (data) => {
        if (!data?.pages) return data
        const patchItems = (arr?: ImageItem[]) => arr?.map((it) => (it.id === id ? { ...it, decision } : it))
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

  return { invalidateLists, patchDecision, setDecision, setDecisionsBulk }
}
