import { test, expect } from '@playwright/test'
import { fileURLToPath } from 'url'
import path from 'path'
import fs from 'fs'

// REAL end-to-end: drive the actual analysis pipeline (photo_audit + build_db)
// from the Re-analyze panel against a copy of a real library, then exercise the
// reorganization (apply -> move del-marked files into <lib>/_rejected/, undo).
// Verifies file moves on disk via Node fs. Opt-in: see playwright.real.config.js.

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const LIBPATH_FILE = path.join(__dirname, '..', '.real', 'libpath.txt')
const ANALYZE_TIMEOUT = Number(process.env.E2E_ANALYZE_TIMEOUT || 900_000)

const libPath = () => fs.readFileSync(LIBPATH_FILE, 'utf8').trim()
const isFile = (dir, f) => fs.statSync(path.join(dir, f)).isFile()
const rootFileCount = (lib) => fs.readdirSync(lib).filter((f) => isFile(lib, f)).length
const rejectedCount = (lib) => {
  const rej = path.join(lib, '_rejected')
  return fs.existsSync(rej) ? fs.readdirSync(rej).filter((f) => isFile(rej, f)).length : 0
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
  expect(rejectedCount(lib)).toBe(0)

  const first = page.locator('.card').first()
  await Promise.all([
    page.waitForResponse((r) =>
      r.url().includes('/api/decisions') && r.request().method() === 'POST'),
    first.getByRole('button', { name: 'Delete' }).click(),
  ])

  // Apply status is queried independently; reload so it sees the pending mark.
  await page.reload()
  const apply = page.locator('.apply-panel')
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/apply') && r.request().method() === 'POST'),
    apply.getByRole('button', { name: /Move \d+ to _rejected/ }).click(),
  ])
  await expect(apply.getByRole('button', { name: /Undo \(\d+ moved\)/ })).toBeVisible()

  // One file physically moved into _rejected/.
  expect(rejectedCount(lib)).toBe(1)
  expect(rootFileCount(lib)).toBe(beforeRoot - 1)

  // ── Undo restores it ────────────────────────────────────────────────────────
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/apply/undo') && r.request().method() === 'POST'),
    apply.getByRole('button', { name: /Undo \(\d+ moved\)/ }).click(),
  ])
  await expect(apply.getByRole('button', { name: /Move \d+ to _rejected/ })).toBeVisible()

  expect(rejectedCount(lib)).toBe(0)
  expect(rootFileCount(lib)).toBe(beforeRoot)
})
