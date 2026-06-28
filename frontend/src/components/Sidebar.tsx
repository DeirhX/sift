import { useState, useEffect, type ReactNode } from 'react'
import ApplyPanel from './ApplyPanel'
import RangeSlider from './RangeSlider'
import FolderTree from './FolderTree'
import FolderInput from './FolderInput'
import {
  renameCluster, mergeClusters, startTask,
  getLibraryFolders, addLibraryFolder, removeLibraryFolder,
} from '../api'
import type { Filters } from '../urlState'
import type { MetaResponse, ClusterFacet, TaskSnapshot } from '../api/types'
import type { UpdateFilter } from '../types'

// Left filter panel. To keep it from growing without bound, the tall/variable
// blocks (scores, people, folders, tags) live in collapsible sections that
// remember their open state and show a one-line summary when closed. The two
// status axes (decision verdict, library/trash lifecycle) are multi-select.

// Single-select segmented control (sort direction).
function Seg(
  { value, options, onChange }: {
    value: string
    options: { v: string; label: string }[]
    onChange: (v: string) => void
  },
) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          className={value === o.v ? 'active' : ''}
          onClick={() => onChange(o.v)}
        >{o.label}</button>
      ))}
    </div>
  )
}

// Multi-select segmented control: any subset of options can be on at once.
function MultiToggle(
  { options, selected, onToggle }: {
    options: { v: string; label: string }[]
    selected: Set<string>
    onToggle: (v: string) => void
  },
) {
  return (
    <div className="seg multi">
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          className={selected.has(o.v) ? 'active' : ''}
          aria-pressed={selected.has(o.v)}
          onClick={() => onToggle(o.v)}
        >{o.label}</button>
      ))}
    </div>
  )
}

// Remember a section's open/closed state across reloads so the user's chosen
// layout sticks. Falls back gracefully when localStorage is unavailable.
function useCollapse(key: string, defaultOpen: boolean) {
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem('flt.open.' + key)
      return v == null ? defaultOpen : v === '1'
    } catch { return defaultOpen }
  })
  const toggle = () => setOpen((o) => {
    const next = !o
    try { localStorage.setItem('flt.open.' + key, next ? '1' : '0') } catch { /* ignore */ }
    return next
  })
  return [open, toggle] as const
}

// Collapsible filter section: a clickable header (with a summary shown only when
// collapsed) over a body that mounts only while open.
function Section(
  { title, summary, sticky, defaultOpen = true, children }: {
    title: string
    summary?: string
    sticky: string
    defaultOpen?: boolean
    children: ReactNode
  },
) {
  const [open, toggle] = useCollapse(sticky, defaultOpen)
  return (
    <div className={'filter-section' + (open ? ' open' : '')}>
      <button type="button" className="section-head" onClick={toggle} aria-expanded={open}>
        <span className="section-chevron" aria-hidden>{open ? '\u25be' : '\u25b8'}</span>
        <span className="section-title">{title}</span>
        {!open && summary ? <span className="section-summary">{summary}</span> : null}
      </button>
      {open && <div className="section-body">{children}</div>}
    </div>
  )
}

// ── Multi-select (de)serialization for the two status axes ────────────────────
// Both filter values stay simple comma-separated strings so URLs and the
// existing Filters type are untouched; these helpers map them to/from a Set.

const DECISION_ORDER = ['none', 'keep', 'del']

function parseDecision(s: string): Set<string> {
  const set = new Set<string>()
  if (!s || s === 'all') return set            // empty selection ⇒ any verdict
  for (const t of s.split(',')) {
    const k = t.trim().toLowerCase()
    if (k === 'keep' || k === 'del') set.add(k)
    else if (k === 'none' || k === 'unmarked' || k === 'new') set.add('none')
  }
  return set
}

function serializeDecision(set: Set<string>): string {
  if (set.size === 0 || set.size >= 3) return 'all'   // none or every verdict ⇒ any
  return DECISION_ORDER.filter((t) => set.has(t)).join(',')
}

function parseShow(s: string): Set<string> {
  if (s === 'any') return new Set(['active', 'trashed'])
  const set = new Set<string>()
  for (const t of (s || '').split(',')) {
    const k = t.trim().toLowerCase()
    if (k === 'active' || k === 'trashed') set.add(k)
  }
  if (!set.size) set.add('active')             // never show nothing: default to library
  return set
}

function serializeShow(set: Set<string>): string {
  const active = set.has('active')
  const trashed = set.has('trashed')
  if (active && trashed) return 'active,trashed'
  if (trashed) return 'trashed'
  return 'active'
}

interface SidebarProps {
  meta?: MetaResponse
  filters: Filters
  updateFilter: UpdateFilter
  toggleInList: (key: 'tags' | 'people', value: string) => void
  resetFilters: () => void
  // The count/label of what the *active view* is showing (photos / scenes /
  // groups), so the footer matches reality instead of always using the grid
  // count — which is 0 in Scenes/Groups view (that query is disabled there).
  shownCount: number
  shownLabel: string
  onPeopleChange?: () => void
  onTaskDone?: (task: TaskSnapshot) => void
}

const PEOPLE_CAP = 10
const TAG_CAP = 14

export default function Sidebar(
  { meta, filters, updateFilter, toggleInList, resetFilters, shownCount, shownLabel, onPeopleChange, onTaskDone }: SidebarProps,
) {
  const clusters = meta?.clusters ?? []
  const tags = meta?.tags ?? []
  const counts = meta?.counts
  const hist = meta?.histograms ?? {}
  const [managePeople, setManagePeople] = useState(false)
  const [peopleExpanded, setPeopleExpanded] = useState(false)
  const [tagQuery, setTagQuery] = useState('')
  const [tagsExpanded, setTagsExpanded] = useState(false)

  // Source-folder management (the catalog's definition). Loaded once; kept in
  // sync locally on add/remove. Each mutation auto-kicks a re-analyze — cheap,
  // since unchanged folders are pure cache hits — so the change actually lands
  // in the catalog. App's task poller shows progress and refreshes on done.
  const [libFolders, setLibFolders] = useState<string[] | null>(null)
  const [folderInput, setFolderInput] = useState('')
  const [folderBusy, setFolderBusy] = useState(false)
  const [folderMsg, setFolderMsg] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getLibraryFolders()
      .then((d) => { if (live) setLibFolders(d.folders) })
      .catch(() => { if (live) setLibFolders([]) })
    return () => { live = false }
  }, [])

  // Indexed photo count for a source folder: sum the directory facet entries
  // (meta.folders) that sit at or below it. A folder added but not yet analyzed
  // sums to 0, which the UI surfaces as "not indexed yet".
  const sepOf = (s: string) => (s.includes('\\') ? '\\' : '/')
  const countForFolder = (lib: string): number => {
    const base = lib.replace(/[\\/]+$/, '')
    const sep = sepOf(base)
    let n = 0
    for (const f of meta?.folders ?? []) {
      if (f.path === base || f.path.startsWith(base + sep)) n += f.count
    }
    return n
  }

  const reanalyze = async (folders: string[], note: string) => {
    if (!folders.length) { setFolderMsg('No folders left — add one to rebuild the catalog.'); return }
    await startTask('analyze_library', { folders })
    setFolderMsg(note)
  }

  const addFolder = async () => {
    const path = folderInput.trim().replace(/[\\/]+$/, '')
    if (!path || folderBusy) return
    setFolderBusy(true); setFolderMsg(null)
    try {
      const d = await addLibraryFolder(path)
      setLibFolders(d.folders)
      setFolderInput('')
      await reanalyze(d.folders, 'Indexing the new folder…')
    } catch (e) {
      setFolderMsg(e instanceof Error ? e.message : String(e))
    } finally { setFolderBusy(false) }
  }

  const removeFolder = async (path: string) => {
    if (folderBusy) return
    if (!window.confirm(
      `Remove this folder from the library?\n\n${path}\n\n` +
      'Its photos leave the catalog on the next index. Your keep/delete ' +
      'decisions are kept and re-bind if you add it back.')) return
    setFolderBusy(true); setFolderMsg(null)
    try {
      const d = await removeLibraryFolder(path)
      setLibFolders(d.folders)
      await reanalyze(d.folders, 'Re-indexing the remaining folders…')
    } catch (e) {
      setFolderMsg(e instanceof Error ? e.message : String(e))
    } finally { setFolderBusy(false) }
  }

  const personLabel = (c: ClusterFacet): string =>
    c.name && c.name.trim() ? c.name : `Person ${c.cluster_id}`

  const saveName = async (cid: number, name: string, prev: string | null) => {
    if ((name || '').trim() === (prev || '').trim()) return
    await renameCluster(cid, name.trim())
    onPeopleChange?.()
  }

  const doMerge = async (from: number, into: string) => {
    if (into === '' || Number(into) === from) return
    const target = clusters.find((c) => c.cluster_id === Number(into))
    const src = clusters.find((c) => c.cluster_id === from)
    if (!src || !target) return
    if (!window.confirm(
      `Merge "${personLabel(src)}" (${src.count}) into "${personLabel(target)}"?`)) return
    await mergeClusters(from, Number(into))
    onPeopleChange?.()
  }

  // Multi-select status axes.
  // "Show every verdict" is stored as an empty set, but we light all three so the
  // control reads as "all on" rather than "nothing selected". Toggling then peels
  // one off; turning the last one off wraps back to all-on (never "show nothing").
  const decRaw = parseDecision(filters.decision)
  const decSel = decRaw.size === 0 ? new Set(DECISION_ORDER) : decRaw
  const toggleDecision = (v: string) => {
    const next = new Set(decSel)
    if (next.has(v)) next.delete(v); else next.add(v)
    updateFilter({ decision: serializeDecision(next) })
  }
  const showSel = parseShow(filters.trash)
  const toggleShow = (v: string) => {
    const next = new Set(showSel)
    if (next.has(v)) next.delete(v); else next.add(v)
    updateFilter({ trash: serializeShow(next) })
  }

  // Collapsed-section summaries.
  const rangeActive =
    (filters.scoreMin > 0 || filters.scoreMax < 1 ? 1 : 0) +
    (filters.sharpMin > 0 || filters.sharpMax < 1 ? 1 : 0) +
    (filters.aesMin > 0 || filters.aesMax < 1 ? 1 : 0) +
    (filters.portraitMin > 0 || filters.portraitMax < 1 ? 1 : 0)
  const scoresSummary = rangeActive ? `${rangeActive} active` : 'Any'
  const peopleSummary = filters.people.length
    ? `${filters.people.length} selected` : `${clusters.length}`
  const tagsSummary = filters.tags.length
    ? `${filters.tags.length} selected` : `${tags.length}`
  const folderSummary = filters.folder
    ? filters.folder.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || filters.folder
    : 'All'

  // People list: full while managing, capped otherwise (with a show-more toggle).
  const peopleShown = (managePeople || peopleExpanded)
    ? clusters : clusters.slice(0, PEOPLE_CAP)

  // Tags: substring-filtered, then capped unless expanded.
  const tagMatches = tagQuery.trim()
    ? tags.filter((t) => t.tag.toLowerCase().includes(tagQuery.trim().toLowerCase()))
    : tags
  const tagsShown = tagsExpanded ? tagMatches : tagMatches.slice(0, TAG_CAP)

  return (
    <aside className="sidebar">
      <div>
        <h1>Photo Audit</h1>
        <div className="sub">{(counts?.total ?? 0).toLocaleString()} indexed · {counts?.dup_groups ?? 0} dup groups</div>
      </div>

      <div className="filter-group">
        <label className="group-label">Sort</label>
        <select value={filters.sort} onChange={(e) => updateFilter({ sort: e.target.value })}>
          <option value="combined">Quality (combined)</option>
          <option value="sharpness">Sharpness</option>
          <option value="aesthetic">Aesthetic</option>
          {meta?.has_portrait && <option value="portrait">Portrait quality</option>}
          <option value="filename">Filename</option>
        </select>
        <Seg
          value={filters.dir}
          options={[{ v: 'desc', label: 'High → Low' }, { v: 'asc', label: 'Low → High' }]}
          onChange={(v) => updateFilter({ dir: v })}
        />
      </div>

      {/* Folders are first-class: this section both *manages* the catalog's
          source folders (add/remove → auto re-analyze) and *filters* by them. */}
      <Section title="Folders" sticky="folders" summary={folderSummary} defaultOpen>
        <div className="folder-manager">
          {libFolders == null ? (
            <div className="folder-empty">Loading folders…</div>
          ) : libFolders.length === 0 ? (
            <div className="folder-empty">No folders yet — add one to build your library.</div>
          ) : (
            <ul className="folder-roots">
              {libFolders.map((f) => {
                const n = countForFolder(f)
                return (
                  <li key={f} className="folder-root" title={f}>
                    <span className="folder-root-name">
                      {f.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || f}
                    </span>
                    <span className={'folder-root-count' + (n > 0 ? '' : ' pending')}>
                      {n > 0 ? n.toLocaleString() : 'not indexed'}
                    </span>
                    <button
                      className="folder-root-x"
                      title="Remove from library (decisions are kept)"
                      disabled={folderBusy}
                      onClick={() => removeFolder(f)}
                    >
                      ×
                    </button>
                  </li>
                )
              })}
            </ul>
          )}

          <div className="folder-add">
            <FolderInput
              value={folderInput}
              onChange={setFolderInput}
              disabled={folderBusy}
              placeholder="Add a folder…"
            />
            <button
              className="btn"
              disabled={folderBusy || !folderInput.trim()}
              onClick={addFolder}
            >
              Add
            </button>
          </div>
          {folderMsg && <div className="folder-msg">{folderMsg}</div>}

          {(meta?.folders?.length ?? 0) > 0 && (
            <>
              <div className="folder-filter-label">Filter by folder</div>
              <FolderTree folders={meta?.folders} filters={filters} updateFilter={updateFilter} embedded />
            </>
          )}
        </div>
      </Section>

      <Section title="Scores" sticky="scores" summary={scoresSummary} defaultOpen={false}>
        <RangeSlider label="Quality range" minKey="scoreMin" maxKey="scoreMax" filters={filters} updateFilter={updateFilter} histogram={hist.combined} />
        <RangeSlider label="Sharpness range" minKey="sharpMin" maxKey="sharpMax" filters={filters} updateFilter={updateFilter} histogram={hist.sharpness} />
        <RangeSlider label="Aesthetic range" minKey="aesMin" maxKey="aesMax" filters={filters} updateFilter={updateFilter} histogram={hist.aesthetic} />
        {meta?.has_portrait && (
          <RangeSlider label="Portrait quality" minKey="portraitMin" maxKey="portraitMax" filters={filters} updateFilter={updateFilter} histogram={hist.portrait} />
        )}
      </Section>

      <div className="filter-group">
        <label className="group-label">Duplicates</label>
        <select value={filters.dupMode} onChange={(e) => updateFilter({ dupMode: e.target.value })}>
          <option value="all">Show all</option>
          <option value="hide-dups">Best of each group</option>
          <option value="groups-only">Only duplicates</option>
          <option value="no-groups">No duplicates</option>
        </select>
      </div>

      {/* Two orthogonal, multi-select axes. Decision is the verdict (None/Keep/Del,
          OR'd; none selected = any). Show is the file lifecycle (Library and/or
          Trash). They compose: e.g. Show=Trash + Decision=Del. */}
      <div className="filter-group">
        <label className="group-label">Decision</label>
        <MultiToggle
          options={[
            { v: 'none', label: 'None' },
            { v: 'keep', label: 'Keep' },
            { v: 'del', label: 'Del' },
          ]}
          selected={decSel}
          onToggle={toggleDecision}
        />
      </div>

      <div className="filter-group">
        <label className="group-label">Show</label>
        <MultiToggle
          options={[
            { v: 'active', label: 'Library' },
            { v: 'trashed', label: 'Trash' },
          ]}
          selected={showSel}
          onToggle={toggleShow}
        />
      </div>

      {clusters.length > 0 && (
        <Section title="People" sticky="people" summary={peopleSummary} defaultOpen>
          <div className="section-tools">
            <button className="link-btn" onClick={() => setManagePeople((v) => !v)}>
              {managePeople ? 'done' : 'manage'}
            </button>
          </div>

          {!managePeople ? (
            <>
              <div className="chip-list">
                {peopleShown.map((c) => (
                  <span
                    key={c.cluster_id}
                    className={'chip' + (filters.people.includes(String(c.cluster_id)) ? ' active' : '')}
                    onClick={() => toggleInList('people', String(c.cluster_id))}
                  >
                    {personLabel(c)}<span className="count">{c.count}</span>
                  </span>
                ))}
              </div>
              {clusters.length > PEOPLE_CAP && (
                <button className="link-btn show-more" onClick={() => setPeopleExpanded((v) => !v)}>
                  {peopleExpanded ? 'Show fewer' : `Show all ${clusters.length}`}
                </button>
              )}
            </>
          ) : (
            <div className="people-manager">
              {clusters.map((c) => (
                <div className="person-row" key={c.cluster_id}>
                  <input
                    className="person-name"
                    defaultValue={c.name || ''}
                    placeholder={`Person ${c.cluster_id}`}
                    onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
                    onBlur={(e) => saveName(c.cluster_id, e.target.value, c.name ?? null)}
                  />
                  <span className="person-count">{c.count}</span>
                  <select
                    className="person-merge"
                    value=""
                    onChange={(e) => { doMerge(c.cluster_id, e.target.value); e.target.value = '' }}
                    title="Merge this person into another"
                  >
                    <option value="">merge →</option>
                    {clusters.filter((o) => o.cluster_id !== c.cluster_id).map((o) => (
                      <option key={o.cluster_id} value={o.cluster_id}>{personLabel(o)}</option>
                    ))}
                  </select>
                </div>
              ))}
              <div className="manager-note">Saved — names survive re-indexing.</div>
            </div>
          )}
        </Section>
      )}

      {tags.length > 0 && (
        <Section title="Tags" sticky="tags" summary={tagsSummary} defaultOpen={false}>
          {tags.length > TAG_CAP && (
            <input
              type="text"
              className="tag-filter"
              placeholder="Filter tags…"
              value={tagQuery}
              onChange={(e) => setTagQuery(e.target.value)}
            />
          )}
          <div className="chip-list">
            {tagsShown.map((t) => (
              <span
                key={t.tag}
                className={'chip' + (filters.tags.includes(t.tag) ? ' active' : '')}
                onClick={() => toggleInList('tags', t.tag)}
              >
                {t.tag}<span className="count">{t.count}</span>
              </span>
            ))}
            {tagsShown.length === 0 && <span className="muted-note">No tags match.</span>}
          </div>
          {tagMatches.length > TAG_CAP && (
            <button className="link-btn show-more" onClick={() => setTagsExpanded((v) => !v)}>
              {tagsExpanded ? 'Show fewer' : `Show all ${tagMatches.length}`}
            </button>
          )}
        </Section>
      )}

      <button className="btn full" onClick={resetFilters}>Reset filters</button>

      <ApplyPanel onTaskDone={onTaskDone} />

      <div className="stats">
        Showing <b>{shownCount.toLocaleString()}</b> {shownLabel}
        {shownLabel === 'photos' && <> of {(counts?.total ?? 0).toLocaleString()}</>}
        <br />
        With faces: <b>{counts?.with_faces ?? 0}</b>
      </div>
    </aside>
  )
}
