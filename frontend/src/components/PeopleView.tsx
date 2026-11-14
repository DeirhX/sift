import { useState } from 'react'
import { faceUrl } from '../api'
import type { Person } from '../api/types'

export const personLabel = (p: Pick<Person, 'cluster_id' | 'name'>): string =>
  p.cluster_id < 0 ? 'Unsorted faces' : p.name?.trim() ? p.name : `Person ${p.cluster_id}`

const plural = (n: number, one: string): string => `${n.toLocaleString()} ${one}${n === 1 ? '' : 's'}`

export function PersonCover({ person, size = 160 }: { person: Person; size?: 96 | 160 | 320 }) {
  const [failed, setFailed] = useState(false)
  if (person.cover_face_id == null || failed) return <span className="person-cover placeholder" aria-hidden="true" />
  return (
    <img className="person-cover" loading="lazy" alt=""
      src={faceUrl(person.cover_face_id, size, person.cover_hash)}
      onError={() => setFailed(true)} />
  )
}

function PersonCard({ person, onOpen }: { person: Person; onOpen: (clusterId: number) => void }) {
  const named = !!person.name?.trim()
  return (
    <button className={'person-card' + (named ? ' named' : '')} onClick={() => onOpen(person.cluster_id)}>
      <PersonCover person={person} />
      <span className="person-card-name">{personLabel(person)}</span>
      <span className="person-card-count">
        {plural(person.faces, 'face')}
        {person.photos !== person.faces && ` · ${plural(person.photos, 'photo')}`}
      </span>
    </button>
  )
}

interface PeopleViewProps {
  people: Person[]
  loading: boolean
  onOpen: (clusterId: number) => void
}

// Everyone found in the library, one card per person. People seen in a single
// photo and never named are mostly passers-by, so they sit in a collapsed
// section instead of burying the people you care about.
export default function PeopleView({ people, loading, onOpen }: PeopleViewProps) {
  const [showOnce, setShowOnce] = useState(false)
  if (loading) return <div className="spinner">Loading people…</div>
  if (people.length === 0) {
    return (
      <div className="empty">
        No faces yet. Re-analyze with face detection turned on to find people.
      </div>
    )
  }

  const unsorted = people.find((p) => p.cluster_id < 0)
  const known = people.filter((p) => p.cluster_id >= 0)
  const once = known.filter((p) => p.faces === 1 && !p.name?.trim())
  const main = known.filter((p) => !once.includes(p))

  return (
    <div className="grid-scroll people-scroll" tabIndex={-1}>
      <div className="people-grid">
        {main.map((p) => <PersonCard key={p.cluster_id} person={p} onOpen={onOpen} />)}
        {unsorted && <PersonCard person={unsorted} onOpen={onOpen} />}
      </div>
      {once.length > 0 && (
        <section className="people-once">
          <button className="link-btn" aria-expanded={showOnce} onClick={() => setShowOnce((v) => !v)}>
            {showOnce ? 'Hide' : 'Show'} {plural(once.length, 'person').replace('persons', 'people')} seen only once
          </button>
          {showOnce && (
            <div className="people-grid">
              {once.map((p) => <PersonCard key={p.cluster_id} person={p} onOpen={onOpen} />)}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
