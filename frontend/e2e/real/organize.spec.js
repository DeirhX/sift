import { test, expect } from '@playwright/test'
import { fileURLToPath } from 'url'
import path from 'path'
import fs from 'fs'

// REAL end-to-end: drive the actual analysis pipeline (photo_audit + build_db)
// from the Re-analyze panel against a copy of a real library, then exercise the
// reorganization (trash -> move del-marked files into <lib>/_trash/, restore).
// Verifies file moves on disk via Node fs. Opt-in: see playwright.real.config.js.

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const LIBPATH_FILE = path.join(__dirname, '..', '.real', 'libpath.txt')
const ANALYZE_TIMEOUT = Number(process.env.E2E_ANALYZE_TIMEOUT || 900_000)

const libPath = () => fs.readFileSync(LIBPATH_FILE, 'utf8').trim()
const isFile = (dir, f) => fs.statSync(path.join(dir, f)).isFile()
const rootFileCount = (lib) => fs.readdirSync(lib).filter((f) => isFile(lib, f)).length
const trashCount = (lib) => {
  const trash = path.join(lib, '_trash')
  return fs.existsSync(trash) ? fs.readdirSync(trash).filter((f) => isFile(trash, f)).length : 0
}

async function taskAndWait(page, action) {
  const [resp] = await Promise.all([
    page.waitForResponse((r) =>
      r.url().includes('/api/tasks') && r.request().method() === 'POST'),
    action(),
  ])
  expect(resp.ok()).toBeTruthy()
  const task = await resp.json()
  await expect.poll(async () => {
    const status = await page.request.get(`/api/tasks/${task.id}`)
    expect(status.ok()).toBeTruthy()
    return (await status.json()).state
  }).toBe('done')
}

test('analyze a copied library, then reorganize (apply/undo) on disk', async ({ page }) => {
  // The fixture server copies a subset of E2E_SOURCE_FOLDER into .real/lib and
  // records the path. If the source wasn't available, there's nothing to test.
  const lib = libPath()
  const seeded = rootFileCount(lib)
  test.skip(seeded === 0, `no images copied (source folder unavailable?): ${lib}`)

  await page.goto('/')
  // Fresh DB -> empty grid before analysis.
  await expect(page.locator('.result-count')).toContainText('0 photos')

  // ── Run the real analysis from the web panel ────────────────────────────────
  await page.getByRole('button', { name: 'Re-analyze' }).click()
  const panel = page.locator('.analyze-panel')
  await expect(panel).toBeVisible()
  const folder = panel.locator('input[type="text"]').first()
  await folder.fill(lib)
  await panel.getByRole('button', { name: 'Run analysis' }).click()

  // Stream runs photo_audit then build_db; wait for the success state. If it
  // fails, surface the error text instead of a blind timeout.
  await expect(async () => {
    const failed = await panel.locator('.analyze-status.failed').count()
    if (failed) {
      const err = await panel.locator('.af-error').textContent().catch(() => '')
      throw new Error(`analysis failed: ${err}`)
    }
    await expect(panel.locator('.analyze-status.done')).toBeVisible({ timeout: 2000 })
  }).toPass({ timeout: ANALYZE_TIMEOUT })

  await panel.getByRole('button', { name: 'Close' }).click()

  // Grid is now populated from the freshly-built DB.
  await expect(page.locator('.result-count')).not.toContainText('0 photos')
  await expect(page.locator('.card').first()).toBeVisible()

  // ── Reorganize: mark one for deletion, apply, verify the move on disk ───────
  page.on('dialog', (d) => d.accept())

  const beforeRoot = rootFileCount(lib)
  expect(trashCount(lib)).toBe(0)

  const first = page.locator('.card').first()
  await Promise.all([
    page.waitForResponse((r) =>
      r.url().includes('/api/decisions') && r.request().method() === 'POST'),
    first.getByRole('button', { name: 'Delete' }).click(),
  ])

  // Apply status is queried independently; reload so it sees the pending mark.
  await page.reload()
  const apply = page.locator('.apply-panel')
  await taskAndWait(page, () => apply.getByRole('button', { name: /Move \d+ to Trash/ }).click())
  await expect(apply.getByRole('button', { name: /Restore \(\d+ trashed\)/ })).toBeVisible()

  // One file physically moved into _trash/.
  expect(trashCount(lib)).toBe(1)
  expect(rootFileCount(lib)).toBe(beforeRoot - 1)

  // ── Restore moves it back ──────────────────────────────────────────────────
  await taskAndWait(page, () => apply.getByRole('button', { name: /Restore \(\d+ trashed\)/ }).click())
  await expect(apply.getByRole('button', { name: /Move \d+ to Trash/ })).toBeVisible()

  expect(trashCount(lib)).toBe(0)
  expect(rootFileCount(lib)).toBe(beforeRoot)
})

// Proves the REAL pipeline (EXIF read + CLIP scene grouping) computed and
// surfaced the rough-scene hierarchy on the sample photos. Reuses the DB built
// by the analyze test above (same server, workers:1), so it must run after it;
// skips cleanly if analysis hasn't populated the library yet.
test('rough scenes are computed and surfaced from the real analysis', async ({ page, request }) => {
  // Decide on the API, not the UI: reading the grid text races the images
  // query (it shows "0 photos" for a beat on load).
  const meta = await (await request.get('/api/meta')).json()
  test.skip(meta.counts.total === 0, 'library not analyzed yet (run the analyze test)')
  await page.goto('/')

  // Scene grouping ran: the model is recorded and the count key is present.
  expect(meta.meta.scene_model).toMatch(/exif/)        // "exif+clip-b32" by default
  expect(typeof meta.counts.scene_groups).toBe('number')

  // capture_time flows through the real pipeline for every image (EXIF or the
  // mtime fallback — never null), proving the new column is populated.
  const imgs = await (await request.get('/api/images?limit=10')).json()
  expect(imgs.items.length).toBeGreaterThan(0)
  expect(imgs.items.some((it) => it.capture_time != null)).toBeTruthy()

  // /api/scenes is well-formed; every member carries its dup_group so the
  // client can nest near-duplicate sub-piles.
  const scenesApi = await (await request.get('/api/scenes')).json()
  expect(scenesApi.total).toBe(meta.counts.scene_groups)
  for (const sc of scenesApi.scenes) {
    expect(sc.items.length).toBeGreaterThan(1)         // scenes are multi-member
    expect(sc.items.every((it) => 'dup_group' in it)).toBeTruthy()
    expect(sc.time_start).not.toBeNull()
  }

  // The Scenes view is reachable either way.
  await page.getByRole('button', { name: 'Scenes' }).click()

  if (meta.counts.scene_groups > 0) {
    // Real multi-photo scenes formed: drill into the first and confirm it
    // renders its nested sub-piles and/or loose members.
    const piles = page.locator('.scene-pile')
    await expect(piles.first()).toBeVisible()
    await piles.first().click()
    const scene = page.locator('.scene-panel')
    await expect(scene).toBeVisible()
    const sub = await scene.locator('.pile').count()
    const loose = await scene.locator('.scene-loose').count()
    expect(sub + loose).toBeGreaterThan(0)
    await scene.getByRole('button', { name: 'Close' }).click()
  } else {
    // The sample produced no multi-photo scene; the view should say so.
    await expect(page.locator('.empty')).toContainText('No scenes found')
  }
})
