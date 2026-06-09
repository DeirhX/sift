import { test, expect } from '@playwright/test'

// Real-browser proof for Part B: against a library of hundreds of scene piles
// (seeded via E2E_SCALE in playwright.scale.config.js), the pile overview must
// keep the DOM bounded no matter how far you scroll, and the mounted slice must
// follow the viewport. This is the hard guarantee the jsdom unit tests can only
// approximate (they stub layout); here it runs in actual Chromium with real
// scrolling and layout.

const MAX_MOUNTED = 80   // generous ceiling; the real window is ~30-40 cells

// data-sg = scene_group; a stable per-pile handle for asserting which slice is
// currently mounted.
const mountedSgs = (grid) =>
  grid.locator('.scene-pile').evaluateAll((els) => els.map((e) => Number(e.getAttribute('data-sg'))))

test('scene pile overview windows the DOM under deep scroll', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Scenes' }).click()

  const grid = page.locator('.grid-scroll')
  await expect(page.locator('.scene-pile').first()).toBeVisible()

  // Page in the full library by repeatedly scrolling to the current bottom
  // (each near-bottom hit prefetches the next page of 30).
  for (let i = 0; i < 12; i++) {
    await grid.evaluate((el) => el.scrollTo(0, el.scrollHeight))
    await page.waitForTimeout(250)
  }

  // Hundreds of piles' worth of rows are reserved: the scroll height dwarfs the
  // viewport, proving the list really is large...
  const { sh, ch } = await grid.evaluate((el) => ({ sh: el.scrollHeight, ch: el.clientHeight }))
  expect(sh).toBeGreaterThan(ch * 5)

  // ...yet the DOM never holds more than a bounded slice.
  await grid.evaluate((el) => el.scrollTo(0, 0))
  await page.waitForTimeout(250)
  const top = await mountedSgs(grid)
  expect(top.length).toBeGreaterThan(0)
  expect(top.length).toBeLessThan(MAX_MOUNTED)
  const topFirst = top[0]

  // Scroll to the bottom: the slice must change (window follows the viewport),
  // the previously-top pile must unmount, and new piles must appear — all while
  // the DOM stays bounded. This is exactly what a naive "render everything"
  // overview cannot do.
  await grid.evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await expect.poll(async () => (await mountedSgs(grid))[0]).not.toBe(topFirst)

  const bottom = await mountedSgs(grid)
  expect(bottom.length).toBeLessThan(MAX_MOUNTED)
  expect(bottom).not.toContain(topFirst)                          // top pile unmounted
  expect(bottom.some((sg) => !top.includes(sg))).toBeTruthy()     // deeper piles mounted
})
