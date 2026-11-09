import { useState, useCallback } from 'react'
import { previewAutocull } from '../api'
import GroupPile from './GroupPile'
import MarkPreview from './MarkPreview'
import WindowedPileGrid from './WindowedPileGrid'
import type { GroupsResponse } from '../api/types'
import type { ReviewMark, ReviewPlan } from '../types'

// Reserved height below each square stack: one quality-pill / decision-count
// row. Keeps pile cells uniform so the grid can window by row.
const GROUP_META_H = 62

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
  onSaveReview: (changes: ReviewMark[]) => Promise<void>
  reviewBusy: boolean
}

// Overview of duplicate groups as stacked photo piles, rendered through the
// windowed pile grid so only on-screen piles mount. Arrow keys move a keyboard
// focus; Enter / click opens the review overlay (`onOpen(dup_group)`), which
// lives at the app root so it is URL-driven / Back-navigable. `reviewOpen` lets
// the app pause grid keys while that overlay is up.
export default function GroupView({ query, onOpen, reviewOpen = false, onSaveReview, reviewBusy }: GroupViewProps) {
  const [loadingPlan, setLoadingPlan] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [plan, setPlan] = useState<ReviewPlan | null>(null)

  const loadPreview = async () => {
    setLoadingPlan(true)
    setError(null)
    setPlan(null)
    try {
      const result = await previewAutocull()
      setPlan({ title: 'Auto-cull preview · entire library', changes: result.changes,
        note: result.review ? `${result.review} groups left for manual comparison.` : undefined })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the cull preview.')
    } finally {
      setLoadingPlan(false)
    }
  }

  const groups = query.data?.pages.flatMap((p) => p.groups) ?? []

  const activate = useCallback((idx: number) => {
    const g = groups[idx]
    if (g) onOpen(g.dup_group)
  }, [groups, onOpen])

  const header = (
    <>
      <div className="group-actionbar">
        <button className="btn primary" disabled={loadingPlan || reviewBusy} onClick={loadPreview}>
          {loadingPlan ? 'Preparing preview…' : 'Preview auto-cull'}
        </button>
        <span className="group-hint">Groups that need review appear first.</span>
      </div>
      {error && <div role="alert" className="af-error">{error}</div>}
      {plan && <MarkPreview plan={plan} busy={reviewBusy} onCancel={() => setPlan(null)}
        onApply={() => { void onSaveReview(plan.changes).then(() => setPlan(null)).catch(() => {}) }} />}
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
