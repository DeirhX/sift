import type { QueryClient } from '@tanstack/react-query'

export const PHOTO_LIST_QUERY_ROOTS = ['images', 'groups', 'scenes'] as const
export const PHOTO_DATA_QUERY_ROOTS = ['images', 'groups', 'scenes', 'meta'] as const
export const TASK_DATA_QUERY_ROOTS = ['images', 'groups', 'scenes', 'meta', 'applyStatus'] as const
export const AUTOCULL_QUERY_ROOTS = ['images', 'groups', 'scenes', 'applyStatus'] as const

type QueryRoot = readonly string[]

export function queryRootIn(queryKey: readonly unknown[], roots: QueryRoot): boolean {
  return typeof queryKey[0] === 'string' && roots.includes(queryKey[0])
}

export function invalidateRoots(qc: QueryClient, roots: QueryRoot): void {
  qc.invalidateQueries({ predicate: (q) => queryRootIn(q.queryKey, roots) })
}
