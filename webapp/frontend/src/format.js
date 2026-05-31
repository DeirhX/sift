// Shared display helpers, so the score formatting and aesthetic fallback are
// defined once instead of being re-declared in every component.

// Two-decimal score, or an en-dash for missing values.
export const fmt = (v) => (v == null ? '–' : v.toFixed(2))

// Aesthetic score, falling back to CLIP-IQA when the PARA score is absent.
export const aestheticScore = (item) => item.para_aesthetic ?? item.clip_iqa
