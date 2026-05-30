// Thin fetch wrappers around the FastAPI backend.

export async function fetchMeta() {
  const r = await fetch('/api/meta')
  if (!r.ok) throw new Error('meta failed')
  return r.json()
}

// Build the /api/images query string from the filter state object.
export function buildImagesQuery(filters, offset, limit) {
  const p = new URLSearchParams()
  p.set('offset', offset)
  p.set('limit', limit)
  p.set('sort', filters.sort)
  p.set('dir', filters.dir)
  p.set('score_min', filters.scoreMin)
  p.set('score_max', filters.scoreMax)
  p.set('sharp_min', filters.sharpMin)
  p.set('sharp_max', filters.sharpMax)
  p.set('aes_min', filters.aesMin)
  p.set('aes_max', filters.aesMax)
  p.set('dup_mode', filters.dupMode)
  p.set('decision', filters.decision)
  if (filters.tags?.length) p.set('tags', filters.tags.join(','))
  if (filters.people?.length) p.set('people', filters.people.join(','))
  if (filters.q?.trim()) p.set('q', filters.q.trim())
  return p.toString()
}

export async function fetchImages(filters, offset, limit) {
  const qs = buildImagesQuery(filters, offset, limit)
  const r = await fetch(`/api/images?${qs}`)
  if (!r.ok) throw new Error('images query failed')
  return r.json()
}

export async function fetchGroups(offset, limit, order = 'size') {
  const p = new URLSearchParams({ offset, limit, order })
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

export async function fetchClusters() {
  const r = await fetch('/api/clusters')
  return r.json()
}

export async function renameCluster(cluster_id, name) {
  await fetch('/api/clusters', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cluster_id, name }),
  })
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

export const thumbUrl = (id) => `/thumb/${id}`
export const fullUrl = (id) => `/img/${id}`
