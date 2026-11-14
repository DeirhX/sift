import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { setDecision as apiSetDecision, setDecisions as apiSetDecisions } from '../api'
import { invalidateRoots, PHOTO_LIST_QUERY_ROOTS, queryRootIn } from '../queryKeys'
import type { ImageItem } from '../api/types'
import type { Decision, ReviewMark } from '../types'
import { toast } from '../toast'

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
  const [undo, setUndo] = useState<ReviewMark[] | null>(null)
  const [reviewBusy, setReviewBusy] = useState(false)
  const [reviewMessage, setReviewMessage] = useState('')

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
      await apiSetDecision(item.hash, next, item.id, item.path)
      qc.invalidateQueries({ queryKey: ['applyStatus'] })
    } catch (error) {
      // The optimistic badge is now a lie: put the old mark back at once (the
      // refetch may fail too if the server is down) and say so, instead of
      // letting the photo quietly flip back later.
      patchDecision(item.id, item.decision ?? null)
      invalidateLists()
      toast.failure(`Couldn’t save the mark for ${item.filename}.`, error)
    }
  }, [patchDecision, invalidateLists, qc])

  const saveReview = async (changes: ReviewMark[], reversing = false) => {
    setReviewBusy(true)
    setReviewMessage('')
    try {
      await apiSetDecisions(changes.flatMap(({ id, hash, path, previous, decision }) =>
        hash == null ? [] : [{ id, hash, path,
          decision: reversing ? previous : decision,
          expected_decision: reversing ? decision : previous,
          guard_only: decision === previous,
        }]))
      const marks = new Map(changes.map((change) => [change.id, reversing ? change.previous : change.decision]))
      patchCache((it) => marks.has(it.id) ? { ...it, decision: marks.get(it.id) } : it)
      invalidateLists()
      qc.invalidateQueries({ queryKey: ['applyStatus'] })
      setUndo(reversing ? null : changes)
      const changed = changes.filter(({ previous, decision }) => previous !== decision).length
      setReviewMessage(reversing ? 'Previous marks restored.' : `Updated ${changed} marks.`)
    } catch (error) {
      invalidateLists()
      setReviewMessage(error instanceof Error ? error.message : 'Could not save these marks.')
      throw error
    } finally {
      setReviewBusy(false)
    }
  }

  return { invalidateLists, patchDecision, patchTrashed, patchTrashedDel, setDecision,
    saveReview, reviewBusy, reviewMessage, canUndoReview: undo != null,
    undoReview: () => { if (undo) void saveReview(undo, true).catch(() => {}) },
  }
}
