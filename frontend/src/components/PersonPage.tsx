import { useEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  assignFaces, deleteFaces, faceUrl, fetchPersonFaces, fullUrl, mergeClusters, renameCluster,
} from '../api'
import type { FaceSort } from '../api'
import type { Person, PersonFace } from '../api/types'
import { fmt, fmtTime } from '../format'
import { PersonCover, personLabel } from './PeopleView'

// A face this unlike the rest of its person is worth a second look.
const ODD_ONE_OUT = 0.35

const SORTS: { value: FaceSort; label: string }[] = [
  { value: 'quality', label: 'Best first' },
  { value: 'date', label: 'Oldest first' },
  { value: 'size', label: 'Largest first' },
  { value: 'match', label: 'Least alike first' },
]

interface PersonPageProps {
  clusterId: number
  people: Person[]
  onBack: () => void
  onOpenPerson: (clusterId: number) => void
  onShowPhotos: (clusterId: number) => void
  // Faces or names changed: refetch everything that renders people.
  onChanged: () => void
}

// One person: every face crop the library has of them. Select faces to move
// them to someone else, split them off as a new person, or drop the ones that
// aren't faces at all. All edits are recorded server-side as overrides, so they
// survive the next analyze.
export default function PersonPage(
  { clusterId, people, onBack, onOpenPerson, onShowPhotos, onChanged }: PersonPageProps,
) {
  const [sort, setSort] = useState<FaceSort>('quality')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<PersonFace | null>(null)
  const lastClicked = useRef<number | null>(null)

  const query = useQuery({
    queryKey: ['people', 'faces', clusterId, sort],
    queryFn: () => fetchPersonFaces(clusterId, sort),
  })
  const data = query.data
  const faces = data?.items ?? []
  const unsorted = clusterId < 0
  const me = people.find((p) => p.cluster_id === clusterId)
  const title = personLabel({ cluster_id: clusterId, name: data?.name ?? me?.name ?? null })
  const others = people.filter((p) => p.cluster_id >= 0 && p.cluster_id !== clusterId)

  // Selection only ever refers to faces that are still on this page.
  useEffect(() => {
    setSelected(new Set())
    lastClicked.current = null
  }, [clusterId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (/^(INPUT|SELECT|TEXTAREA)$/.test((e.target as HTMLElement).tagName)) return
      if (preview) setPreview(null)
      else if (selected.size) setSelected(new Set())
      else onBack()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [preview, selected, onBack])

  const run = async (action: () => Promise<unknown>, after?: () => void) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      setSelected(new Set())
      onChanged()
      after?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That change could not be saved.')
    } finally {
      setBusy(false)
    }
  }

  const toggle = (face: PersonFace, e: MouseEvent) => {
    const index = faces.findIndex((f) => f.id === face.id)
    // Read the anchor now: the updater below may run after it is overwritten.
    const anchor = lastClicked.current
    const range = e.shiftKey && anchor != null
    setSelected((prev) => {
      const next = new Set(prev)
      if (range) {
        const from = faces.findIndex((f) => f.id === anchor)
        const [lo, hi] = from < index ? [from, index] : [index, from]
        for (const f of faces.slice(Math.max(0, lo), hi + 1)) next.add(f.id)
      } else if (next.has(face.id)) next.delete(face.id)
      else next.add(face.id)
      return next
    })
    lastClicked.current = face.id
  }

  const ids = [...selected]
  // Moving every face away empties this person, so there is no page to stay on.
  const leaveIfEmptied = () => { if (ids.length >= (data?.total ?? 0)) onBack() }

  const moveSelected = (value: string) => {
    if (!value || !ids.length) return
    if (value === '__new__') {
      const name = window.prompt('Name for the new person (optional):')
      if (name === null) return
      void run(() => assignFaces(ids, { new_person: true, name: name.trim() || undefined }), leaveIfEmptied)
    } else {
      void run(() => assignFaces(ids, { cluster_id: Number(value) }), leaveIfEmptied)
    }
  }

  const removeSelected = () => {
    if (!ids.length) return
    if (!window.confirm(`Remove ${ids.length} face box${ids.length === 1 ? '' : 'es'}? `
      + 'The photos are not touched; these just stop counting as faces.')) return
    void run(() => deleteFaces(ids), leaveIfEmptied)
  }

  const mergeInto = (value: string) => {
    const target = others.find((p) => p.cluster_id === Number(value))
    if (!target) return
    if (!window.confirm(`Merge all of "${title}" into "${personLabel(target)}"?`)) return
    void run(() => mergeClusters(clusterId, target.cluster_id), () => onOpenPerson(target.cluster_id))
  }

  const absorb = (other: Person) => {
    if (!window.confirm(`Merge "${personLabel(other)}" (${other.faces}) into "${title}"?`)) return
    void run(() => mergeClusters(other.cluster_id, clusterId))
  }

  const rename = (value: string) => {
    const name = value.trim()
    if (name === (data?.name ?? '').trim()) return
    void run(() => renameCluster(clusterId, name || null))
  }

  return (
    <div className="person-page">
      <div className="person-head">
        <button className="btn" onClick={onBack}>← People</button>
        {me && <PersonCover person={me} size={96} />}
        {unsorted ? <h2 className="person-title">{title}</h2> : (
          <input
            className="person-title-input"
            aria-label="Person name"
            key={`${clusterId}:${data?.name ?? ''}`}
            defaultValue={data?.name ?? ''}
            placeholder={`Person ${clusterId} — add a name`}
            disabled={busy}
            onBlur={(e) => rename(e.currentTarget.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
          />
        )}
        <span className="person-total">
          {data ? `${data.total.toLocaleString()} face${data.total === 1 ? '' : 's'}` : ''}
        </span>
        <span className="spacer" />
        <label className="person-sort">
          <span>Sort</span>
          <select value={sort} onChange={(e) => setSort(e.currentTarget.value as FaceSort)}>
            {SORTS.filter((s) => s.value !== 'match' || data?.has_match).map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </label>
        {!unsorted && (
          <>
            <button className="btn" onClick={() => onShowPhotos(clusterId)}>Show photos</button>
            <select className="person-merge-into" aria-label="Merge this person into another"
              value="" disabled={busy || others.length === 0} onChange={(e) => mergeInto(e.currentTarget.value)}>
              <option value="">Merge into…</option>
              {others.map((p) => <option key={p.cluster_id} value={p.cluster_id}>{personLabel(p)} ({p.faces})</option>)}
            </select>
          </>
        )}
      </div>

      {(data?.similar.length ?? 0) > 0 && (
        <div className="person-similar">
          <span className="person-similar-label">Possibly the same person</span>
          {data!.similar.map((p) => (
            <span className="similar-chip" key={p.cluster_id}>
              <button className="similar-open" title="Open this person" onClick={() => onOpenPerson(p.cluster_id)}>
                <PersonCover person={p} size={96} />
                <span>{personLabel(p)} <em>{p.faces}</em></span>
                <span className="similar-score" title="How alike the two sets of faces are">
                  {Math.round(p.similarity * 100)}%
                </span>
              </button>
              <button className="btn" disabled={busy} onClick={() => absorb(p)}>Merge in</button>
            </span>
          ))}
        </div>
      )}

      {error && <div role="alert" className="af-error">{error}</div>}

      <div className="person-actionbar" role="toolbar" aria-label="Selected faces">
        {selected.size === 0 ? (
          <>
            <span className="person-hint">Click faces to select them (Shift-click for a range).</span>
            {faces.length > 0 && (
              <button className="link-btn" onClick={() => setSelected(new Set(faces.map((f) => f.id)))}>Select all</button>
            )}
          </>
        ) : (
          <>
            <strong>{selected.size} selected</strong>
            <select aria-label="Move selected faces to" value="" disabled={busy}
              onChange={(e) => moveSelected(e.currentTarget.value)}>
              <option value="">Move to…</option>
              <option value="__new__">＋ New person…</option>
              {others.map((p) => <option key={p.cluster_id} value={p.cluster_id}>{personLabel(p)} ({p.faces})</option>)}
            </select>
            <button className="btn danger" disabled={busy} onClick={removeSelected}>Not a face</button>
            <button className="link-btn" disabled={busy} onClick={() => setSelected(new Set())}>Clear</button>
          </>
        )}
      </div>

      {query.isLoading ? <div className="spinner">Loading faces…</div>
        : query.isError ? <div className="empty" role="alert">Couldn’t load this person’s faces.</div>
        : faces.length === 0 ? <div className="empty">No faces here any more.</div>
        : (
          <div className="grid-scroll face-scroll" tabIndex={-1}>
            <div className="face-grid">
              {faces.map((f) => {
                const on = selected.has(f.id)
                const odd = f.match != null && f.match < ODD_ONE_OUT
                return (
                  <div key={f.id} className={'face-tile' + (on ? ' selected' : '') + (odd ? ' odd' : '')}>
                    <button className="face-tile-pick" aria-pressed={on}
                      aria-label={`${on ? 'Deselect' : 'Select'} face from ${f.filename}`}
                      onClick={(e) => toggle(f, e)}>
                      <img loading="lazy" alt="" draggable={false}
                        src={faceUrl(f.id, 160, `${f.hash ?? ''}-${Math.round(f.bbox[0])}-${Math.round(f.bbox[1])}`)} />
                    </button>
                    {odd && <span className="face-flag" title={`Looks unlike the rest of this person (match ${fmt(f.match)})`}>?</span>}
                    {f.decision === 'del' && <span className="face-flag del" title="This photo is marked for deletion">del</span>}
                    <button className="face-tile-open" title={`Open ${f.filename}`}
                      aria-label={`Open photo ${f.filename}`} onClick={() => setPreview(f)}>⤢</button>
                  </div>
                )
              })}
            </div>
            {data && data.total > faces.length && (
              <p className="person-hint">Showing the first {faces.length.toLocaleString()} of {data.total.toLocaleString()} faces.</p>
            )}
          </div>
        )}

      {preview && (
        <div className="face-preview" role="dialog" aria-label={preview.filename} onClick={() => setPreview(null)}>
          <img src={fullUrl(preview.image_id, preview.hash)} alt={preview.filename} />
          <div className="face-preview-meta" onClick={(e) => e.stopPropagation()}>
            <strong>{preview.filename}</strong>
            {fmtTime(preview.capture_time) && <span>{fmtTime(preview.capture_time)}</span>}
            <span>sharp {fmt(preview.sharp)}</span>
            {preview.match != null && <span>match {fmt(preview.match)}</span>}
            <button className="btn" onClick={() => setPreview(null)}>Close</button>
          </div>
        </div>
      )}
    </div>
  )
}
