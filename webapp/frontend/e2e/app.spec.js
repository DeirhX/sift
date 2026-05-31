import { test, expect } from '@playwright/test'

// Full-stack smoke tests: real React build talking to the real FastAPI backend
// over HTTP, against the seeded fixture library (see serve_fixture.py).

test('grid loads the seeded photos', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.result-count')).toContainText('photos')
  // 6 images are seeded; at least the first page should render cards.
  await expect(page.locator('.card').first()).toBeVisible()
  expect(await page.locator('.card').count()).toBeGreaterThan(1)
})

test('caption search narrows the grid', async ({ page }) => {
  await page.goto('/')
  await page.getByPlaceholder('Search captions…').fill('cat')
  await page.getByPlaceholder('Search captions…').press('Enter')
  await expect(page.getByText('a cat on a sofa')).toBeVisible()
  await expect(page.getByText('a sunny beach')).toHaveCount(0)
  await expect(page.locator('.card')).toHaveCount(1)
})

test('keep decision persists across reload', async ({ page }) => {
  await page.goto('/')
  const first = page.locator('.card').first()
  await first.getByRole('button', { name: 'Keep' }).click()
  await expect(first.locator('.badge-decision.keep')).toBeVisible()

  await page.reload()
  // Default sort is combined desc, so the same photo is first again.
  await expect(page.locator('.card').first().locator('.badge-decision.keep')).toBeVisible()
})

test('lightbox opens on a thumbnail and closes on Escape', async ({ page }) => {
  await page.goto('/')
  await page.locator('.card .thumb-wrap').first().click()
  const lightbox = page.locator('.lightbox')
  await expect(lightbox).toBeVisible()
  await expect(lightbox.locator('img')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(lightbox).toHaveCount(0)
})

test('groups view shows duplicate piles and opens a review', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Groups' }).click()
  const pile = page.locator('.pile').first()
  await expect(pile).toBeVisible()
  await pile.click()
  await expect(page.locator('.review-panel')).toBeVisible()
})
