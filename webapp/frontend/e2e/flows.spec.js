import { test, expect } from '@playwright/test'

// Broader coverage of common user interactions. These run against the same
// shared server/DB (workers:1), so every test that creates a decision MUST
// clean up after itself (toggle marks back to null / Clear) so later tests
// start from a predictable state. Keep marks are harmless to the apply flow.

// Auto-accept confirm() dialogs (apply/undo use them).
test.beforeEach(({ page }) => {
  page.on('dialog', (d) => d.accept())
})

// Click something that issues a decision and wait for the server to ack it,
// so reloads / follow-up assertions don't race the fire-and-forget POST.
async function decideAndWait(page, action) {
  const [resp] = await Promise.all([
    page.waitForResponse((r) =>
      r.url().includes('/api/decisions') && r.request().method() === 'POST'),
    action(),
  ])
  expect(resp.ok()).toBeTruthy()
}

const sidebar = (page) => page.locator('.sidebar')
const cards = (page) => page.locator('.card')

test.describe('sidebar filters', () => {
  test('sort direction reorders the grid', async ({ page }) => {
    await page.goto('/')
    await expect(cards(page).first()).toContainText('a sunny beach') // desc: 0.95 first
    await sidebar(page).getByRole('button', { name: 'Low → High' }).click()
    await expect(cards(page).first()).toContainText('a blurry shot')  // asc: 0.20 first
  })

  test('dup-mode "only duplicates" shows just the group members', async ({ page }) => {
    await page.goto('/')
    await sidebar(page)
      .locator('select', { has: page.locator('option[value="groups-only"]') })
      .selectOption('groups-only')
    await expect(cards(page)).toHaveCount(2) // beach + beach2 share dup group 0
  })

  test('tag chip narrows to tagged photos', async ({ page }) => {
    await page.goto('/')
    await sidebar(page).locator('.chip', { hasText: 'beach' }).first().click()
    await expect(cards(page)).toHaveCount(2)
    await sidebar(page).getByRole('button', { name: 'Reset filters' }).click()
    await expect(cards(page).count()).resolves.toBeGreaterThan(2)
  })

  test('people chip narrows to a person', async ({ page }) => {
    await page.goto('/')
    await sidebar(page).locator('.chip', { hasText: 'Alice' }).first().click()
    await expect(cards(page)).toHaveCount(1) // only the portrait has Alice
    await expect(cards(page).first()).toContainText('a portrait of a person')
  })

  test('decision filter shows only matching verdicts', async ({ page }) => {
    await page.goto('/')
    const first = cards(page).first()
    await decideAndWait(page, () => first.getByRole('button', { name: 'Delete' }).click())

    await sidebar(page).getByRole('button', { name: 'Del', exact: true }).click()
    await expect(cards(page)).toHaveCount(1)
    await expect(cards(page).first().locator('.badge-decision.del')).toBeVisible()

    // cleanup: back to all + clear the mark
    await sidebar(page).getByRole('button', { name: 'All', exact: true }).click()
    await decideAndWait(page, () => cards(page).first().getByRole('button', { name: 'Delete' }).click())
  })
})

test.describe('decisions', () => {
  test('delete then toggle-clear', async ({ page }) => {
    await page.goto('/')
    const first = cards(page).first()
    await decideAndWait(page, () => first.getByRole('button', { name: 'Delete' }).click())
    await expect(first.locator('.badge-decision.del')).toBeVisible()
    // Clicking the active verdict again clears it.
    await decideAndWait(page, () => first.getByRole('button', { name: 'Delete' }).click())
    await expect(first.locator('.badge-decision')).toHaveCount(0)
  })
})

test.describe('lightbox', () => {
  test('navigates with arrows and buttons, shows locations, closes', async ({ page }) => {
    await page.goto('/')
    await cards(page).first().locator('.thumb-wrap').click()
    const lb = page.locator('.lightbox')
    await expect(lb).toBeVisible()
    await expect(lb.locator('.lb-info b')).toContainText('beach.jpg')

    await page.keyboard.press('ArrowRight')
    await expect(lb.locator('.lb-info b')).toContainText('portrait.jpg')
    await lb.locator('.lb-nav.prev').click()
    await expect(lb.locator('.lb-info b')).toContainText('beach.jpg')

    // Unique image -> a single filesystem location is listed.
    await expect(lb.locator('.lb-locations')).toContainText('beach.jpg')

    await page.keyboard.press('Escape')
    await expect(lb).toHaveCount(0)
  })

  test('keyboard "d" marks the open photo for deletion', async ({ page }) => {
    await page.goto('/')
    await cards(page).first().locator('.thumb-wrap').click()
    await expect(page.locator('.lightbox')).toBeVisible()
    await decideAndWait(page, () => page.keyboard.press('d'))
    await page.keyboard.press('Escape')
    await expect(cards(page).first().locator('.badge-decision.del')).toBeVisible()
    // cleanup
    await decideAndWait(page, () => cards(page).first().getByRole('button', { name: 'Delete' }).click())
  })
})

test.describe('group review', () => {
  test('keep-best/clear, filmstrip selection and click-to-zoom', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'Groups' }).click()
    await page.locator('.pile').first().click()
    const panel = page.locator('.review-panel')
    await expect(panel).toBeVisible()

    // Bulk: keep best, delete the rest -> strip shows both flag types.
    await panel.getByRole('button', { name: 'Keep best · delete rest' }).click()
    await expect(panel.locator('.strip-flag.keep')).toBeVisible()
    await expect(panel.locator('.strip-flag.del')).toBeVisible()

    // Clear wipes the marks back out (keeps DB clean for later tests).
    await panel.getByRole('button', { name: 'Clear', exact: true }).click()
    await expect(panel.locator('.strip-flag')).toHaveCount(0)

    // Filmstrip selection swaps the hero preview.
    const firstName = await panel.locator('.herobar-name').textContent()
    await panel.locator('.strip-thumb').nth(1).click()
    await expect(panel.locator('.herobar-name')).not.toHaveText(firstName)

    // Click-to-zoom opens the full viewer with its member strip.
    await panel.locator('.review-hero').click()
    await expect(page.locator('.lightbox .lb-strip')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.lightbox')).toHaveCount(0)
  })
})

test.describe('people management', () => {
  test('rename a person and see it reflected', async ({ page }) => {
    await page.goto('/')
    // Wait for meta to render the People section before opening the manager.
    await expect(sidebar(page).locator('.chip', { hasText: 'Alice' })).toBeVisible()
    // The manage/done toggle is a <button> nested in a <label>; target by class.
    const toggle = sidebar(page).locator('.link-btn')
    await toggle.click()
    const input = sidebar(page).locator('.person-name').first()
    await input.fill('Alice Renamed')
    const [resp] = await Promise.all([
      page.waitForResponse((r) =>
        r.url().includes('/api/clusters') && r.request().method() === 'POST'),
      input.press('Enter'),
    ])
    expect(resp.ok()).toBeTruthy()
    await toggle.click()
    await expect(sidebar(page).locator('.chip', { hasText: 'Alice Renamed' })).toBeVisible()

    // cleanup: rename back
    await toggle.click()
    const again = sidebar(page).locator('.person-name').first()
    await again.fill('Alice')
    await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/clusters')),
      again.press('Enter'),
    ])
    await toggle.click()
  })
})

test.describe('face editing', () => {
  test('clicking a face box opens and closes the inline editor', async ({ page }) => {
    await page.goto('/')
    const faceCard = cards(page).filter({ has: page.locator('.face-box') }).first()
    await faceCard.locator('.face-box').first().click()
    await expect(faceCard.locator('.face-editor')).toBeVisible()
    await faceCard.getByRole('button', { name: 'Close' }).click()
    await expect(faceCard.locator('.face-editor')).toHaveCount(0)
  })
})

test.describe('re-analyze panel', () => {
  test('opens, prefills the folder and closes', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'Re-analyze' }).click()
    const panel = page.locator('.analyze-panel')
    await expect(panel).toBeVisible()
    await expect(panel.locator('input[type="text"]')).not.toHaveValue('')
    await panel.getByRole('button', { name: 'Close' }).click()
    await expect(panel).toHaveCount(0)
  })
})

test.describe('apply / undo', () => {
  test('move del-marked files to _rejected and undo', async ({ page }) => {
    await page.goto('/')
    const first = cards(page).first()
    await decideAndWait(page, () => first.getByRole('button', { name: 'Delete' }).click())

    // Apply status is queried independently; reload so it sees the pending mark.
    await page.reload()
    const apply = page.locator('.apply-panel')
    await apply.getByRole('button', { name: /Move \d+ to _rejected/ }).click()
    await expect(apply.getByRole('button', { name: /Undo \(\d+ moved\)/ })).toBeVisible()

    await apply.getByRole('button', { name: /Undo \(\d+ moved\)/ }).click()
    await expect(apply.getByRole('button', { name: /Move \d+ to _rejected/ })).toBeVisible()

    // cleanup: clear the mark and reset apply status
    await decideAndWait(page, () => cards(page).first().getByRole('button', { name: 'Delete' }).click())
  })
})
