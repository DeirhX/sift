// Thin fetch wrappers around the FastAPI backend.

export async function fetchMeta() {
  const r = await fetch('/api/meta')
  if (!r.ok) throw new Error('meta failed')
  return r.json()
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

export async function fetchImages(filters, offset, limit) {
  const qs = buildImagesQuery(filters, offset, limit)
  const r = await fetch(`/api/images?${qs}`)
  if (!r.ok) throw new Error('images query failed')
  return r.json()
}

export async function fetchGroups(filters, offset, limit, order = 'size') {
  const p = new URLSearchParams()
  p.set('offset', offset)
  p.set('limit', limit)
  p.set('order', order)
  appendFilters(p, filters)
  const r = await fetch(`/api/groups?${p.toString()}`)
  if (!r.ok) throw new Error('groups query failed')
  return r.json()
}

export async function setDecision(hash, decision) {
  await fetch('/api/decisions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hash, decision }),
  })
}

export async function renameCluster(cluster_id, name) {
  await fetch('/api/clusters', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cluster_id, name }),
  })
}

// Reassign every face of one or more clusters into another person.
export async function mergeClusters(from, into) {
  const r = await fetch('/api/clusters/merge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from, into }),
  })
  if (!r.ok) throw new Error('merge failed')
  return r.json()
}

// Move a single face box to an existing person, or to a brand-new one.
export async function assignFace(faceId, { cluster_id, new_person, name } = {}) {
  const r = await fetch(`/api/faces/${faceId}/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cluster_id, new_person, name }),
  })
  if (!r.ok) throw new Error('assign failed')
  return r.json()
}

// Remove a false-positive face box.
export async function deleteFace(faceId) {
  const r = await fetch(`/api/faces/${faceId}`, { method: 'DELETE' })
  if (!r.ok) throw new Error('delete face failed')
  return r.json()
}

export async function autocullGroups() {
  const r = await fetch('/api/groups/autocull', { method: 'POST' })
  if (!r.ok) throw new Error('autocull failed')
  return r.json()
}

export async function fetchApplyStatus() {
  const r = await fetch('/api/apply/status')
  if (!r.ok) throw new Error('apply status failed')
  return r.json()
}

export async function applyDecisions() {
  const r = await fetch('/api/apply', { method: 'POST' })
  if (!r.ok) throw new Error('apply failed')
  return r.json()
}

export async function undoApply() {
  const r = await fetch('/api/apply/undo', { method: 'POST' })
  if (!r.ok) throw new Error('undo failed')
  return r.json()
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

export const thumbUrl = (id) => `/thumb/${id}`
export const fullUrl = (id) => `/img/${id}`
