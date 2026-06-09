import { useEffect, useRef, useState } from 'react'

// Log-scale bounds for the granularity slider: "new scene after a pause of…"
// 15s (very fine, many scenes) up to 1h (very coarse, a few big sessions).
const MIN_GAP = 15
const MAX_GAP = 3600
const LN_RANGE = Math.log(MAX_GAP / MIN_GAP)

const posToGap = (pos: number): number =>
  Math.round(MIN_GAP * Math.exp(LN_RANGE * (pos / 100)))
const gapToPos = (gap: number): number => {
  const g = Math.min(MAX_GAP, Math.max(MIN_GAP, gap))
  return Math.round((Math.log(g / MIN_GAP) / LN_RANGE) * 100)
}

function fmtGap(s: number): string {
  if (s < 90) return `${Math.round(s)}s`
  const mins = s / 60
  return mins < 10 ? `${mins.toFixed(1).replace(/\.0$/, '')} min` : `${Math.round(mins)} min`
}

interface Props {
  gap: number               // currently applied gap (seconds), from meta
  sceneCount: number
  onCommit: (gap: number) => void
}

// Single "scene granularity" knob. There is no objectively correct scene gap
// (the capture-time gap distribution is a smooth continuum), so this is an
// explicit user choice: drag toward Coarser to merge sessions, Finer to split
// them. Commits are debounced so dragging doesn't spam the server.
export default function SceneGranularity({ gap, sceneCount, onCommit }: Props) {
  const [pos, setPos] = useState(() => gapToPos(gap))
  const timer = useRef<number | undefined>(undefined)

  // Resync when the applied gap changes externally (e.g. meta loads / re-analyze).
  useEffect(() => { setPos(gapToPos(gap)) }, [gap])
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const onChange = (next: number) => {
    setPos(next)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => onCommit(posToGap(next)), 350)
  }

  return (
    <div className="scene-gran" title="No gap is objectively 'correct' — pick the scene size you want to review at">
      <span className="scene-gran-label">Scene size</span>
      <span className="scene-gran-end">Finer</span>
      <input
        type="range"
        min={0}
        max={100}
        value={pos}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label="Scene granularity"
      />
      <span className="scene-gran-end">Coarser</span>
      <span className="scene-gran-val">
        ≈ {fmtGap(posToGap(pos))} pause · {sceneCount.toLocaleString()} scenes
      </span>
    </div>
  )
}
