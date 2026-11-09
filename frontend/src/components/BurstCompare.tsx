import { useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { fullUrl } from '../api'
import type { GroupedImageItem } from '../api/types'
import { fmt } from '../format'
import DecisionBadge from './DecisionBadge'

const fit = { scale: 1, x: 0, y: 0 }
const clamp = (v: number, max: number) => Math.max(-max, Math.min(max, v))
const faceArea = (face: GroupedImageItem['faces'][number]) =>
  (face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1])

export default function BurstCompare({ pinned, alternative, position, count, onStep }: {
  pinned: GroupedImageItem
  alternative: GroupedImageItem
  position: number
  count: number
  onStep: (direction: number) => void
}) {
  const pairRef = useRef<HTMLDivElement>(null)
  const [camera, setCamera] = useState(fit)
  const [faceMode, setFaceMode] = useState(false)
  const [failed, setFailed] = useState<string[]>([])
  const drag = useRef<{ pointer: number; x: number; y: number; w: number; h: number } | null>(null)
  const leftFace = [...pinned.faces].sort((a, b) => faceArea(b) - faceArea(a))[0]
  const rightFace = [...alternative.faces].sort((a, b) => faceArea(b) - faceArea(a))
    .find((face) => face.cluster_id === leftFace?.cluster_id)
  const canZoomToFace = leftFace?.id != null && rightFace?.id != null
  const showFace = faceMode && canZoomToFace

  useEffect(() => {
    const pair = pairRef.current
    if (!pair) return
    const onWheel = (e: WheelEvent) => {
      if (!(e.target instanceof HTMLImageElement)) return
      e.preventDefault()
      const rect = e.target.getBoundingClientRect()
      if (!rect.width || !rect.height) return
      const x = (e.clientX - rect.left) / rect.width - 0.5
      const y = (e.clientY - rect.top) / rect.height - 0.5
      setCamera((prev) => {
        const scale = Math.max(1, Math.min(8, prev.scale * Math.exp(-e.deltaY * 0.0015)))
        if (scale === 1) return fit
        return { scale,
          x: clamp(prev.x + (prev.scale - scale) * x, scale / 2),
          y: clamp(prev.y + (prev.scale - scale) * y, scale / 2) }
      })
    }
    pair.addEventListener('wheel', onWheel, { passive: false })
    return () => pair.removeEventListener('wheel', onWheel)
  }, [])

  const panStart = (e: ReactPointerEvent<HTMLImageElement>) => {
    if (camera.scale === 1 || e.button !== 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { pointer: e.pointerId, x: e.clientX, y: e.clientY,
      w: rect.width / camera.scale, h: rect.height / camera.scale }
  }
  const panMove = (e: ReactPointerEvent<HTMLImageElement>) => {
    const start = drag.current
    if (!start || start.pointer !== e.pointerId) return
    const dx = (e.clientX - start.x) / start.w
    const dy = (e.clientY - start.y) / start.h
    drag.current = { ...start, x: e.clientX, y: e.clientY }
    setCamera((prev) => ({ ...prev, x: clamp(prev.x + dx, prev.scale / 2), y: clamp(prev.y + dy, prev.scale / 2) }))
  }
  const zoomBy = (factor: number) => {
    setCamera((prev) => {
      const scale = Math.max(1, Math.min(8, prev.scale * factor))
      return scale === 1 ? fit : { ...prev, scale, x: clamp(prev.x, scale / 2), y: clamp(prev.y, scale / 2) }
    })
  }
  const pane = (item: GroupedImageItem, label: string, faceId?: number) => {
    const src = fullUrl(item.id, item.hash, showFace ? faceId : undefined)
    return (
      <div className="compare-pane">
        <div className="compare-label">
          <strong>{label}</strong><span>{item.filename}</span>
          <span title="Keeper score">Score {fmt(item.keeper_score ?? item.combined)}</span>
          <DecisionBadge decision={item.decision} />
        </div>
        <div className="compare-image">
          {failed.includes(src) ? <span>Preview unavailable</span> : (
            <img key={src} src={src} alt={item.filename} draggable={false}
              style={{ transform: `translate(${camera.x * 100}%, ${camera.y * 100}%) scale(${camera.scale})` }}
              className={camera.scale > 1 ? 'pannable' : ''}
              onPointerDown={panStart} onPointerMove={panMove} onLostPointerCapture={() => { drag.current = null }}
              onDoubleClick={() => setCamera(fit)} onError={() => setFailed((prev) => [...prev, src])} />
          )}
        </div>
      </div>
    )
  }

  return (
    <section className="burst-compare" aria-label="Burst comparison">
      <div className="compare-controls">
        <button className="btn" disabled={position <= 0} onClick={() => onStep(-1)}>Previous frame</button>
        <span>{position + 1} / {count} alternatives</span>
        <button className="btn" disabled={position + 1 >= count} onClick={() => onStep(1)}>Next frame</button>
        <span className="spacer" />
        <button className="btn" aria-label="Zoom both out" disabled={camera.scale <= 1} onClick={() => zoomBy(0.8)}>−</button>
        <button className="btn" aria-label="Zoom both in" disabled={camera.scale >= 8} onClick={() => zoomBy(1.25)}>+</button>
        <button className="btn" onClick={() => { setCamera(fit); setFaceMode(false) }}>Fit both</button>
        <button className="btn" aria-pressed={showFace} disabled={!canZoomToFace}
          title={canZoomToFace ? undefined : 'The same person needs a detected face in both frames'}
          onClick={() => { setFaceMode(!showFace); setCamera(fit) }}>Zoom to face</button>
      </div>
      <div className="compare-pair" ref={pairRef}>
        {pane(pinned, 'Suggested keeper', leftFace?.id)}
        {pane(alternative, 'Alternative', rightFace?.id)}
      </div>
    </section>
  )
}
