// Serialize filter + view state to/from the URL query string so views are
// shareable and survive reloads. Only non-default values are written, keeping
// URLs short. Unknown/legacy params are ignored.

export const DEFAULT_FILTERS = {
  sort: 'combined',
  dir: 'desc',
  scoreMin: 0, scoreMax: 1,
  sharpMin: 0, sharpMax: 1,
  aesMin: 0, aesMax: 1,
  portraitMin: 0, portraitMax: 1,
  dupMode: 'all',
  decision: 'all',
  tags: [],
  people: [],
  folder: '',
  folderRecursive: true,
  q: '',
}

const NUM_KEYS = ['scoreMin', 'scoreMax', 'sharpMin', 'sharpMax', 'aesMin', 'aesMax',
  'portraitMin', 'portraitMax']
const LIST_KEYS = ['tags', 'people']
const STR_KEYS = ['sort', 'dir', 'dupMode', 'decision', 'folder', 'q']
const BOOL_KEYS = ['folderRecursive']

// Read filters + view out of a query string (e.g. window.location.search).
export function parseState(search) {
  const p = new URLSearchParams(search)
  const filters = { ...DEFAULT_FILTERS }

  for (const k of STR_KEYS) {
    const v = p.get(k)
    if (v != null) filters[k] = v
  }
  for (const k of NUM_KEYS) {
    const v = p.get(k)
    if (v != null) {
      const n = parseFloat(v)
      if (!Number.isNaN(n)) filters[k] = n
    }
  }
  for (const k of LIST_KEYS) {
    const v = p.get(k)
    if (v) filters[k] = v.split(',').filter(Boolean)
  }
  for (const k of BOOL_KEYS) {
    const v = p.get(k)
    if (v != null) filters[k] = v !== 'false' && v !== '0'
  }

  const view = p.get('view') === 'groups' ? 'groups' : 'grid'
  return { filters, view }
}

// Build a query string holding only values that differ from the defaults.
export function buildSearch(filters, view) {
  const p = new URLSearchParams()

  for (const k of [...STR_KEYS, ...NUM_KEYS]) {
    if (filters[k] !== DEFAULT_FILTERS[k]) p.set(k, String(filters[k]))
  }
  for (const k of LIST_KEYS) {
    if (filters[k] && filters[k].length) p.set(k, filters[k].join(','))
  }
  for (const k of BOOL_KEYS) {
    if (filters[k] !== DEFAULT_FILTERS[k]) p.set(k, String(filters[k]))
  }
  if (view !== 'grid') p.set('view', view)

  const s = p.toString()
  return s ? `?${s}` : ''
}
