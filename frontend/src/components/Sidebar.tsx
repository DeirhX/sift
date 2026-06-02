import { useState } from 'react'
import ApplyPanel from './ApplyPanel'
import RangeSlider from './RangeSlider'
import FolderTree from './FolderTree'
import { renameCluster, mergeClusters } from '../api'
import type { Filters } from '../urlState'
import type { MetaResponse, ClusterFacet } from '../api/types'
import type { UpdateFilter } from '../types'

// Left filter panel: sort, score/sharpness/aesthetic ranges, dup mode,
// decision, people clusters, tags, plus a reset and export link.

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
          className={value === o.v ? 'active' : ''}
          onClick={() => onChange(o.v)}
        >{o.label}</button>
      ))}
    </div>
  )
}

interface SidebarProps {
  meta?: MetaResponse
  filters: Filters
  updateFilter: UpdateFilter
  toggleInList: (key: 'tags' | 'people', value: string) => void
  resetFilters: () => void
  total: number
  onPeopleChange?: () => void
}

export default function Sidebar(
  { meta, filters, updateFilter, toggleInList, resetFilters, total, onPeopleChange }: SidebarProps,
) {
  const clusters = meta?.clusters ?? []
  const tags = meta?.tags ?? []
  const counts = meta?.counts
  const hist = meta?.histograms ?? {}
  const [managePeople, setManagePeople] = useState(false)

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

      <RangeSlider label="Quality range" minKey="scoreMin" maxKey="scoreMax" filters={filters} updateFilter={updateFilter} histogram={hist.combined} />
      <RangeSlider label="Sharpness range" minKey="sharpMin" maxKey="sharpMax" filters={filters} updateFilter={updateFilter} histogram={hist.sharpness} />
      <RangeSlider label="Aesthetic range" minKey="aesMin" maxKey="aesMax" filters={filters} updateFilter={updateFilter} histogram={hist.aesthetic} />
      {meta?.has_portrait && (
        <RangeSlider label="Portrait quality" minKey="portraitMin" maxKey="portraitMax" filters={filters} updateFilter={updateFilter} histogram={hist.portrait} />
      )}

      <div className="filter-group">
        <label className="group-label">Duplicates</label>
        <select value={filters.dupMode} onChange={(e) => updateFilter({ dupMode: e.target.value })}>
          <option value="all">Show all</option>
          <option value="hide-dups">Best of each group</option>
          <option value="groups-only">Only duplicates</option>
          <option value="no-groups">No duplicates</option>
        </select>
      </div>

      <div className="filter-group">
        <label className="group-label">Decision</label>
        <Seg
          value={filters.decision}
          options={[
            { v: 'all', label: 'All' },
            { v: 'keep', label: 'Keep' },
            { v: 'del', label: 'Del' },
            { v: 'unmarked', label: 'New' },
            { v: 'notdel', label: 'Hide del' },
          ]}
          onChange={(v) => updateFilter({ decision: v })}
        />
      </div>

      {clusters.length > 0 && (
        <div className="filter-group">
          <label className="group-label">
            People
            <button className="link-btn" onClick={() => setManagePeople((v) => !v)}>
              {managePeople ? 'done' : 'manage'}
            </button>
          </label>

          {!managePeople ? (
            <div className="chip-list">
              {clusters.map((c) => (
                <span
                  key={c.cluster_id}
                  className={'chip' + (filters.people.includes(String(c.cluster_id)) ? ' active' : '')}
                  onClick={() => toggleInList('people', String(c.cluster_id))}
                >
                  {personLabel(c)}<span className="count">{c.count}</span>
                </span>
              ))}
            </div>
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
              <div className="manager-note">Edits last until the next DB ingest.</div>
            </div>
          )}
        </div>
      )}

      <FolderTree folders={meta?.folders} filters={filters} updateFilter={updateFilter} />

      {tags.length > 0 && (
        <div className="filter-group">
          <label className="group-label">Tags</label>
          <div className="chip-list">
            {tags.map((t) => (
              <span
                key={t.tag}
                className={'chip' + (filters.tags.includes(t.tag) ? ' active' : '')}
                onClick={() => toggleInList('tags', t.tag)}
              >
                {t.tag}<span className="count">{t.count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <button className="btn full" onClick={resetFilters}>Reset filters</button>

      <a className="btn full" href="/api/export" style={{ textAlign: 'center', textDecoration: 'none' }}>
        Export decisions
      </a>

      <ApplyPanel />

      <div className="stats">
        Showing <b>{total.toLocaleString()}</b> of {(counts?.total ?? 0).toLocaleString()}<br />
        With faces: <b>{counts?.with_faces ?? 0}</b>
      </div>
    </aside>
  )
}
