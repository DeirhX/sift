// Dual-handle range slider with a value-distribution histogram behind the
// track. Two overlapping native range inputs supply the handles (accessible,
// keyboard-friendly); the histogram bars are dimmed outside the selected span.
// Operates on the fixed [0,1] domain that all our scores live in.

const STEP = 0.01

export default function RangeSlider({ label, minKey, maxKey, filters, updateFilter, histogram }) {
  const lo = filters[minKey]
  const hi = filters[maxKey]

  const bins = histogram ?? []
  const peak = bins.length ? Math.max(...bins, 1) : 1

  const onLo = (e) => {
    const v = Math.min(parseFloat(e.target.value), hi)
    updateFilter({ [minKey]: v })
  }
  const onHi = (e) => {
    const v = Math.max(parseFloat(e.target.value), lo)
    updateFilter({ [maxKey]: v })
  }

  const pct = (v) => `${v * 100}%`

  return (
    <div className="filter-group rs">
      <div className="rs-head">
        <label className="group-label">{label}</label>
        <span className="rs-vals">{lo.toFixed(2)} – {hi.toFixed(2)}</span>
      </div>

      <div className="rs-track">
        {bins.length > 0 && (
          <div className="rs-hist">
            {bins.map((c, i) => {
              const center = (i + 0.5) / bins.length
              const inRange = center >= lo && center <= hi
              return (
                <span
                  key={i}
                  className={'rs-bar' + (inRange ? '' : ' dim')}
                  style={{ height: `${(c / peak) * 100}%` }}
                />
              )
            })}
          </div>
        )}
        <div className="rs-fill" style={{ left: pct(lo), right: pct(1 - hi) }} />
        <input
          type="range" min="0" max="1" step={STEP} value={lo}
          onChange={onLo} className="rs-input rs-lo"
          aria-label={`${label} minimum`}
        />
        <input
          type="range" min="0" max="1" step={STEP} value={hi}
          onChange={onHi} className="rs-input rs-hi"
          aria-label={`${label} maximum`}
        />
      </div>
    </div>
  )
}
