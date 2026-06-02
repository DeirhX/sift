// App-wide UI helper types shared across components. DTO shapes come from
// ./api/types (codegen'd); this file is for the callback/prop contracts the
// component tree passes around.
import type { Filters } from './urlState'
import type { ImageItem } from './api/types'

export type Decision = 'keep' | 'del'

// Apply (or toggle off) a keep/delete verdict for a photo. Implementations take
// the full item so they can read its current decision and content hash.
export type DecisionFn = (item: ImageItem, decision: Decision) => void

// Resolve a face cluster id to a display name, or null when the cluster has no
// human-assigned name (callers fall back to "Person N").
export type PersonName = (clusterId: number | null | undefined) => string | null

// Patch a subset of the filter state.
export type UpdateFilter = (patch: Partial<Filters>) => void

// Keys of Filters whose value is a number (the range-slider endpoints).
export type NumericFilterKey = {
  [K in keyof Filters]: Filters[K] extends number ? K : never
}[keyof Filters]

// One row of a bulk keep/del/clear operation over a group's members.
export interface BulkDecision {
  id: number
  hash: string | null
  decision: Decision | null
}

export type BulkDecisionFn = (decisions: BulkDecision[]) => void

// Lightbox index control: a new index, null to close, or a functional updater.
export type LightboxIndex = number | null | ((prev: number) => number)
export type SetLightboxIndex = (v: LightboxIndex) => void
