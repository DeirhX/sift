// Thin fetch wrappers around the FastAPI backend.

// Fetch + ok-check + JSON parse, the shape almost every endpoint shares.
async function jsonFetch(url, opts) {
  const r = await fetch(url, opts)
  if (!r.ok) throw new Error(`request to ${url} failed`)
  return r.json()
}

// Request init for a JSON POST body.
const jsonBody = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export function fetchMeta() {
  return jsonFetch('/api/meta')
}

// Shared image-level filter params, applied to both grid and groups queries.
function appendFilters(p, filters) {
  p.set('score_min', filters.scoreMin)
  p.set('score_max', filters.scoreMax)
  p.set('sharp_min', filters.sharpMin)
  p.set('sharp_max', filters.sharpMax)
  p.set('aes_min', filters.aesMin)
  p.set('aes_max', filters.aesMax)
  if (filters.portraitMin > 0) p.set('portrait_min', filters.portraitMin)
  if (filters.portraitMax < 1) p.set('portrait_max', filters.portraitMax)
  p.set('decision', filters.decision)
  if (filters.tags?.length) p.set('tags', filters.tags.join(','))
  if (filters.people?.length) p.set('people', filters.people.join(','))
  if (filters.q?.trim()) p.set('q', filters.q.trim())
}

// Build the /api/images query string from the filter state object.
export function buildImagesQuery(filters, offset, limit) {
  const p = new URLSearchParams()
  p.set('offset', offset)
  p.set('limit', limit)
  p.set('sort', filters.sort)
  p.set('dir', filters.dir)
  p.set('dup_mode', filters.dupMode)
  appendFilters(p, filters)
  return p.toString()
}

export function fetchImages(filters, offset, limit) {
  return jsonFetch(`/api/images?${buildImagesQuery(filters, offset, limit)}`)
}

export function fetchGroups(filters, offset, limit, order = 'size') {
  const p = new URLSearchParams()
  p.set('offset', offset)
  p.set('limit', limit)
  p.set('order', order)
  appendFilters(p, filters)
  return jsonFetch(`/api/groups?${p.toString()}`)
}

// Fire-and-forget: optimistic UI owns rollback, so these don't await a result.
export async function setDecision(hash, decision) {
  await fetch('/api/decisions', jsonBody({ hash, decision }))
}

export async function renameCluster(cluster_id, name) {
  await fetch('/api/clusters', jsonBody({ cluster_id, name }))
}

// Reassign every face of one or more clusters into another person.
export function mergeClusters(from, into) {
  return jsonFetch('/api/clusters/merge', jsonBody({ from, into }))
}

// Move a single face box to an existing person, or to a brand-new one.
export function assignFace(faceId, { cluster_id, new_person, name } = {}) {
  return jsonFetch(`/api/faces/${faceId}/assign`, jsonBody({ cluster_id, new_person, name }))
}

// Remove a false-positive face box.
export function deleteFace(faceId) {
  return jsonFetch(`/api/faces/${faceId}`, { method: 'DELETE' })
}

export function autocullGroups() {
  return jsonFetch('/api/groups/autocull', { method: 'POST' })
}

export function fetchApplyStatus() {
  return jsonFetch('/api/apply/status')
}

export function applyDecisions() {
  return jsonFetch('/api/apply', { method: 'POST' })
}

export function undoApply() {
  return jsonFetch('/api/apply/undo', { method: 'POST' })
}

// ── Re-analysis (run photo_audit + build_db from the web, stream output) ──────
export async function startAnalyze(params) {
  const r = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!r.ok) {
    const msg = await r.json().catch(() => ({}))
    throw new Error(msg.detail || 'analyze failed to start')
  }
  return r.json()
}

export async function analyzeStatus() {
  const r = await fetch('/api/analyze/status')
  return r.json()
}

export async function cancelAnalyze() {
  await fetch('/api/analyze/cancel', { method: 'POST' })
}

export const analyzeStreamUrl = '/api/analyze/stream'

// Every path holding the exact same bytes as this image (content-hash match).
export async function fetchLocations(id) {
  const r = await fetch(`/api/images/${id}/locations`)
  if (!r.ok) throw new Error('locations failed')
  return r.json()
}

// Open a file (selected in its folder) or a directory in the OS file manager.
export async function revealPath(path) {
  const r = await fetch('/api/reveal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) throw new Error('reveal failed')
  return r.json()
}

export const thumbUrl = (id) => `/thumb/${id}`
export const fullUrl = (id) => `/img/${id}`
