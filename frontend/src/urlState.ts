// Serialize filter + view + overlay-nav state to/from the URL query string so
// views are shareable and survive reloads. Only non-default values are written,
// keeping URLs short. Unknown/legacy params are ignored.
//
// The "nav" object captures whatever overlay is open on top of the list so the
// browser Back button and shared links treat it as real navigation:
//   null                                       — no overlay (plain list)
//   { kind:'lightbox', imgId }                 — grid full-detail viewer
//   { kind:'group', refId, imgId, zoom }       — a duplicate-group review
//   { kind:'scene', refId, imgId, zoom }       — a whole-scene review
// `imgId` is the focused photo (added once you select/arrow; absent means the
// review's default hero). `zoom` flags the full-size viewer open inside a
// review. URL params: grp=, scn=, img=, zoom=1.

export type View = 'grid' | 'groups' | 'scenes'

export interface Filters {
  sort: string
  dir: string
  scoreMin: number
  scoreMax: number
  sharpMin: number
  sharpMax: number
  aesMin: number
  aesMax: number
  portraitMin: number
  portraitMax: number
  dupMode: string
  decision: string
  trash: string
  tags: string[]
  people: string[]
  folder: string
  folderRecursive: boolean
  q: string
}

export interface Nav {
  kind: 'lightbox' | 'group' | 'scene'
  refId?: number | null
  imgId?: number | null
  zoom?: boolean
}

export interface AppState {
  filters: Filters
  view: View
  nav: Nav | null
}

export const DEFAULT_FILTERS: Filters = {
  sort: 'combined',
  dir: 'desc',
  scoreMin: 0, scoreMax: 1,
  sharpMin: 0, sharpMax: 1,
  aesMin: 0, aesMax: 1,
  portraitMin: 0, portraitMax: 1,
  dupMode: 'all',
  decision: 'all',
  trash: 'active',
  tags: [],
  people: [],
  folder: '',
  folderRecursive: true,
  q: '',
}

const NUM_KEYS = ['scoreMin', 'scoreMax', 'sharpMin', 'sharpMax', 'aesMin', 'aesMax',
  'portraitMin', 'portraitMax'] as const
const LIST_KEYS = ['tags', 'people'] as const
const STR_KEYS = ['sort', 'dir', 'dupMode', 'decision', 'trash', 'folder', 'q'] as const
const BOOL_KEYS = ['folderRecursive'] as const

// Read filters + view out of a query string (e.g. window.location.search).
export function parseState(search: string): AppState {
  const p = new URLSearchParams(search)
  const filters: Filters = { ...DEFAULT_FILTERS }
  // Controlled dynamic write: each key group is typed, so the cast only loosens
  // the index, not the value types being assigned.
  const f = filters as unknown as Record<string, string | number | boolean | string[]>

  for (const k of STR_KEYS) {
    const v = p.get(k)
    if (v != null) f[k] = v
  }
  for (const k of NUM_KEYS) {
    const v = p.get(k)
    if (v != null) {
      const n = parseFloat(v)
      if (!Number.isNaN(n)) f[k] = n
    }
  }
  for (const k of LIST_KEYS) {
    const v = p.get(k)
    if (v) f[k] = v.split(',').filter(Boolean)
  }
  for (const k of BOOL_KEYS) {
    const v = p.get(k)
    if (v != null) f[k] = v !== 'false' && v !== '0'
  }

  // Migrate legacy single-axis decision values to the two-axis model so old
  // bookmarks (and a session open across the upgrade) don't land on the wrong
  // view: 'trash' → the Trash lifecycle; 'notdel' (a removed convenience) → All.
  if (filters.decision === 'trash') { filters.decision = 'all'; filters.trash = 'trashed' }
  else if (filters.decision === 'notdel') { filters.decision = 'all' }

  const rawView = p.get('view')
  const view: View = rawView === 'groups' || rawView === 'scenes' ? rawView : 'grid'

  return { filters, view, nav: parseNav(p) }
}

// Pull the overlay-nav object out of the parsed params (see header comment).
function parseNav(p: URLSearchParams): Nav | null {
  const num = (v: string | null): number | null => {
    if (v == null || v === '') return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }
  const imgId = num(p.get('img'))
  const zoom = p.get('zoom') === '1'
  const grp = num(p.get('grp'))
  if (grp != null) return { kind: 'group', refId: grp, imgId, zoom }
  const scn = num(p.get('scn'))
  if (scn != null) return { kind: 'scene', refId: scn, imgId, zoom }
  if (imgId != null) return { kind: 'lightbox', imgId }
  return null
}

// Build a query string holding only values that differ from the defaults,
// plus any open overlay (nav). Param order is fixed so equal states stringify
// identically (callers compare URLs to avoid redundant history writes).
export function buildSearch(filters: Filters, view: View, nav: Nav | null = null): string {
  const p = new URLSearchParams()
  const def = DEFAULT_FILTERS as unknown as Record<string, string | number | boolean | string[]>
  const f = filters as unknown as Record<string, string | number | boolean | string[]>

  for (const k of [...STR_KEYS, ...NUM_KEYS]) {
    if (f[k] !== def[k]) p.set(k, String(f[k]))
  }
  for (const k of LIST_KEYS) {
    if (filters[k] && filters[k].length) p.set(k, filters[k].join(','))
  }
  for (const k of BOOL_KEYS) {
    if (f[k] !== def[k]) p.set(k, String(f[k]))
  }
  if (view !== 'grid') p.set('view', view)

  if (nav) {
    if (nav.kind === 'group') p.set('grp', String(nav.refId))
    else if (nav.kind === 'scene') p.set('scn', String(nav.refId))
    if (nav.imgId != null) p.set('img', String(nav.imgId))
    if (nav.zoom) p.set('zoom', '1')
  }

  const s = p.toString()
  return s ? `?${s}` : ''
}
