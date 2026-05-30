import ApplyPanel from './ApplyPanel.jsx'
import RangeSlider from './RangeSlider.jsx'

// Left filter panel: sort, score/sharpness/aesthetic ranges, dup mode,
// decision, people clusters, tags, plus a reset and export link.

function Seg({ value, options, onChange }) {
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

export default function Sidebar({ meta, filters, updateFilter, toggleInList, resetFilters, total }) {
  const clusters = meta?.clusters ?? []
  const tags = meta?.tags ?? []
  const counts = meta?.counts ?? {}
  const hist = meta?.histograms ?? {}

  const personLabel = (c) =>
    c.name && c.name.trim() ? c.name : `Person ${c.cluster_id}`

  return (
    <aside className="sidebar">
      <div>
        <h1>Photo Audit</h1>
        <div className="sub">{(counts.total ?? 0).toLocaleString()} indexed · {counts.dup_groups ?? 0} dup groups</div>
      </div>

      <div className="filter-group">
        <label className="group-label">Sort</label>
        <select value={filters.sort} onChange={(e) => updateFilter({ sort: e.target.value })}>
          <option value="combined">Quality (combined)</option>
          <option value="sharpness">Sharpness</option>
          <option value="aesthetic">Aesthetic</option>
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
          ]}
          onChange={(v) => updateFilter({ decision: v })}
        />
      </div>

      {clusters.length > 0 && (
        <div className="filter-group">
          <label className="group-label">People</label>
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
        </div>
      )}

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
        Showing <b>{total.toLocaleString()}</b> of {(counts.total ?? 0).toLocaleString()}<br />
        With faces: <b>{counts.with_faces ?? 0}</b>
      </div>
    </aside>
  )
}
