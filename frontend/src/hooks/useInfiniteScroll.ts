import { useEffect } from 'react'
import type { RefObject } from 'react'

// The page-query slice the sentinel observer needs (structurally assignable
// from a react-query useInfiniteQuery result).
interface PageQuery {
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => unknown
}

// Fetch the next page when a sentinel element near the list end scrolls into
// view. One definition shared by the pile views (SceneView, GroupView) so the
// IntersectionObserver wiring can't drift between them. `rootMargin` pre-loads
// before the sentinel is actually visible.
export function useInfiniteScroll(
  ref: RefObject<HTMLElement | null>,
  { hasNextPage, isFetchingNextPage, fetchNextPage }: PageQuery,
): void {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage()
      }
    }, { rootMargin: '600px' })
    io.observe(el)
    return () => io.disconnect()
  }, [ref, hasNextPage, isFetchingNextPage, fetchNextPage])
}
