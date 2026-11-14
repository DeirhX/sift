import { useInfiniteQuery } from '@tanstack/react-query'
import { fetchImages, fetchGroups, fetchScenes } from '../api'
import type { Filters, View } from '../urlState'

export const PAGE = 60
export const GROUP_PAGE = 30
export const SCENE_PAGE = 30

interface Page {
  offset: number
  limit: number
  total: number
}

// Offset of the page after `last`, or undefined when `last` was the final one.
export function nextOffset(last: Page): number | undefined {
  const next = last.offset + last.limit
  return next < last.total ? next : undefined
}

// The three paged lists behind the Grid / Groups / Scenes views. Each is keyed
// by its root name + the filters (see queryKeys.ts: optimistic patches and
// invalidation find them by that root), and only the visible view fetches.
export function useLibraryLists(filters: Filters, view: View) {
  const images = useInfiniteQuery({
    queryKey: ['images', filters],
    queryFn: ({ pageParam }) => fetchImages(filters, pageParam, PAGE),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
    enabled: view === 'grid',
  })

  const groups = useInfiniteQuery({
    queryKey: ['groups', filters],
    queryFn: ({ pageParam }) => fetchGroups(filters, pageParam, GROUP_PAGE, 'review'),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
    enabled: view === 'groups',
  })

  const scenes = useInfiniteQuery({
    queryKey: ['scenes', filters],
    queryFn: ({ pageParam }) => fetchScenes(filters, pageParam, SCENE_PAGE),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
    enabled: view === 'scenes',
  })

  return { images, groups, scenes }
}
