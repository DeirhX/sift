// Thin fetch wrappers around the FastAPI backend. Response shapes come from the
// codegen'd OpenAPI types (src/api/types) so they can't drift from the server.
import type { Filters } from './urlState'
import type {
  MetaResponse, ImagesResponse, GroupsResponse, ScenesResponse,
  LocationsResponse, RootsResponse, FsCompleteResponse, OkResponse,
  MergeResponse, AssignFaceResponse, AutocullResponse, ApplyStatusResponse,
  ApplyResponse, UndoResponse, AnalyzeStatus,
} from './api/types'

// Fetch + ok-check + JSON parse, the shape almost every endpoint shares.
async function jsonFetch<T>(url: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(url, opts)
  if (!r.ok) throw new Error(`request to ${url} failed`)
  return r.json() as Promise<T>
}

// Request init for a JSON POST body.
const jsonBody = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export function fetchMeta(): Promise<MetaResponse> {
  return jsonFetch<MetaResponse>('/api/meta')
}

// Shared image-level filter params, applied to both grid and groups queries.
function appendFilters(p: URLSearchParams, filters: Filters): void {
  p.set('score_min', String(filters.scoreMin))
  p.set('score_max', String(filters.scoreMax))
  p.set('sharp_min', String(filters.sharpMin))
  p.set('sharp_max', String(filters.sharpMax))
  p.set('aes_min', String(filters.aesMin))
  p.set('aes_max', String(filters.aesMax))
  if (filters.portraitMin > 0) p.set('portrait_min', String(filters.portraitMin))
  if (filters.portraitMax < 1) p.set('portrait_max', String(filters.portraitMax))
  p.set('decision', filters.decision)
  if (filters.tags?.length) p.set('tags', filters.tags.join(','))
  if (filters.people?.length) p.set('people', filters.people.join(','))
  if (filters.folder) {
    p.set('folder', filters.folder)
    // Backend defaults to recursive; only send the flag when turning it off.
    if (!filters.folderRecursive) p.set('folder_recursive', 'false')
  }
  if (filters.q?.trim()) p.set('q', filters.q.trim())
}

// Build the /api/images query string from the filter state object.
export function buildImagesQuery(filters: Filters, offset: number, limit: number): string {
  const p = new URLSearchParams()
  p.set('offset', String(offset))
  p.set('limit', String(limit))
  p.set('sort', filters.sort)
  p.set('dir', filters.dir)
  p.set('dup_mode', filters.dupMode)
  appendFilters(p, filters)
  return p.toString()
}

export function fetchImages(filters: Filters, offset: number, limit: number): Promise<ImagesResponse> {
  return jsonFetch<ImagesResponse>(`/api/images?${buildImagesQuery(filters, offset, limit)}`)
}

export function fetchGroups(
  filters: Filters, offset: number, limit: number, order = 'size',
): Promise<GroupsResponse> {
  const p = new URLSearchParams()
  p.set('offset', String(offset))
  p.set('limit', String(limit))
  p.set('order', order)
  appendFilters(p, filters)
  return jsonFetch<GroupsResponse>(`/api/groups?${p.toString()}`)
}

export function fetchScenes(
  filters: Filters, offset: number, limit: number, order = 'time',
): Promise<ScenesResponse> {
  const p = new URLSearchParams()
  p.set('offset', String(offset))
  p.set('limit', String(limit))
  p.set('order', order)
  appendFilters(p, filters)
  return jsonFetch<ScenesResponse>(`/api/scenes?${p.toString()}`)
}

// Fire-and-forget: optimistic UI owns rollback, so these don't await a result.
export async function setDecision(hash: string, decision: string | null): Promise<void> {
  await fetch('/api/decisions', jsonBody({ hash, decision }))
}

export async function renameCluster(cluster_id: number, name: string | null): Promise<void> {
  await fetch('/api/clusters', jsonBody({ cluster_id, name }))
}

// Reassign every face of one or more clusters into another person.
export function mergeClusters(from: number | number[], into: number): Promise<MergeResponse> {
  return jsonFetch<MergeResponse>('/api/clusters/merge', jsonBody({ from, into }))
}

export interface AssignFaceOpts {
  cluster_id?: number
  new_person?: boolean
  name?: string
}

// Move a single face box to an existing person, or to a brand-new one.
export function assignFace(
  faceId: number, { cluster_id, new_person, name }: AssignFaceOpts = {},
): Promise<AssignFaceResponse> {
  return jsonFetch<AssignFaceResponse>(
    `/api/faces/${faceId}/assign`, jsonBody({ cluster_id, new_person, name }))
}

// Remove a false-positive face box.
export function deleteFace(faceId: number): Promise<OkResponse> {
  return jsonFetch<OkResponse>(`/api/faces/${faceId}`, { method: 'DELETE' })
}

export function autocullGroups(): Promise<AutocullResponse> {
  return jsonFetch<AutocullResponse>('/api/groups/autocull', { method: 'POST' })
}

export function fetchApplyStatus(): Promise<ApplyStatusResponse> {
  return jsonFetch<ApplyStatusResponse>('/api/apply/status')
}

export function applyDecisions(): Promise<ApplyResponse> {
  return jsonFetch<ApplyResponse>('/api/apply', { method: 'POST' })
}

export function undoApply(): Promise<UndoResponse> {
  return jsonFetch<UndoResponse>('/api/apply/undo', { method: 'POST' })
}

// ── Re-analysis (run sift analyze + index from the web, stream output) ────────
export async function startAnalyze(params: unknown): Promise<AnalyzeStatus> {
  const r = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!r.ok) {
    const msg = await r.json().catch(() => ({} as { detail?: string }))
    throw new Error((msg as { detail?: string }).detail || 'analyze failed to start')
  }
  return r.json() as Promise<AnalyzeStatus>
}

export async function analyzeStatus(): Promise<AnalyzeStatus> {
  const r = await fetch('/api/analyze/status')
  return r.json() as Promise<AnalyzeStatus>
}

export async function cancelAnalyze(): Promise<void> {
  await fetch('/api/analyze/cancel', { method: 'POST' })
}

export const analyzeStreamUrl = '/api/analyze/stream'

// Every path holding the exact same bytes as this image (content-hash match).
export async function fetchLocations(id: number): Promise<LocationsResponse> {
  const r = await fetch(`/api/images/${id}/locations`)
  if (!r.ok) throw new Error('locations failed')
  return r.json() as Promise<LocationsResponse>
}

// Open a file (selected in its folder) or a directory in the OS file manager.
export async function revealPath(path: string): Promise<OkResponse> {
  const r = await fetch('/api/reveal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) throw new Error('reveal failed')
  return r.json() as Promise<OkResponse>
}

// ── Settings: photo roots (reveal guardrail) ──────────────────────────────────
export async function getRoots(): Promise<RootsResponse> {
  const r = await fetch('/api/settings/roots')
  if (!r.ok) throw new Error('failed to load roots')
  return r.json() as Promise<RootsResponse>
}

export async function addRoot(path: string): Promise<RootsResponse> {
  const r = await fetch('/api/settings/roots', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) {
    const msg = await r.json().catch(() => ({} as { detail?: string }))
    throw new Error((msg as { detail?: string }).detail || 'failed to add folder')
  }
  return r.json() as Promise<RootsResponse>
}

export async function removeRoot(path: string): Promise<RootsResponse> {
  const r = await fetch('/api/settings/roots', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) throw new Error('failed to remove folder')
  return r.json() as Promise<RootsResponse>
}

// Server-side directory autocomplete for the settings folder field. Returns
// { entries: string[], truncated: bool }. The browser can't enumerate the FS,
// hence the round-trip.
export async function fsComplete(q: string): Promise<FsCompleteResponse> {
  const r = await fetch(`/api/fs/complete?q=${encodeURIComponent(q || '')}`)
  if (!r.ok) return { entries: [], truncated: false }
  return r.json() as Promise<FsCompleteResponse>
}

// Thumbnails/originals are cached aggressively and keyed by image id, but a
// rebuild can renumber ids so an id may point at a different photo. Append the
// content hash as a cache-buster so the URL changes exactly when the underlying
// image does (and stays stable — cacheable — when it doesn't).
export const thumbUrl = (id: number, v?: string | null): string => `/thumb/${id}${v ? `?v=${v}` : ''}`
export const fullUrl = (id: number, v?: string | null): string => `/img/${id}${v ? `?v=${v}` : ''}`
