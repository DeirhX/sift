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
    await expect(lb.locator('.lb-name')).toContainText('beach.jpg')

    await page.keyboard.press('ArrowRight')
    await expect(lb.locator('.lb-name')).toContainText('portrait.jpg')
    await lb.locator('.lb-nav.prev').click()
    await expect(lb.locator('.lb-name')).toContainText('beach.jpg')

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

    // Bulk: keep best, delete the rest. Del'd members are live-culled from the
    // strip the instant they're marked, so reveal them to verify both flag types.
    await panel.getByRole('button', { name: 'Keep best · delete rest' }).click()
    await panel.getByRole('button', { name: /Show culled/ }).click()
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

test.describe('scenes (rough hierarchy)', () => {
  test('opening a scene shows the same hero+filmstrip review as a group', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'Scenes' }).click()

    // Two multi-photo scenes; the lone blurry shot is a singleton (no scene).
    const piles = page.locator('.scene-pile')
    await expect(piles).toHaveCount(2)

    // Scenes are time-ordered: scene 0 (the beach burst) comes first and
    // advertises its single near-duplicate set.
    const first = piles.first()
    await expect(first).toContainText('1 near-dup set')
    await first.click()

    // The scene opens straight into the unified review — an open image up top
    // and a GROUPED filmstrip. With exactly one near-duplicate set, that set
    // auto-expands on open (a lone ×N tile would be pointless), so the beach
    // pair shows as a 2-thumb cluster and the loose city shot is a plain thumb.
    const review = page.locator('.review-panel')
    await expect(review).toBeVisible()
    await expect(review).toContainText('Scene #')
    await expect(review.locator('.review-hero img')).toBeVisible()
    await expect(review.locator('.strip-cluster')).toHaveCount(1)
    await expect(review.locator('.strip-cluster .strip-thumb')).toHaveCount(2)
    await expect(review.locator('.strip-grouptile')).toHaveCount(0)
    await expect(review.locator('.strip-flatgroup')).toHaveCount(0)
    // The whole-scene "keep best · delete rest" must NOT be offered.
    await expect(review.getByRole('button', { name: 'Keep best · delete rest' }))
      .toHaveCount(0)

    // The "▾ N" badge on the cluster collapses it to the medoid tile, which
    // carries the "▸ N" badge. Clicking the photo only previews its medoid —
    // it must NOT expand (no surprise reflow); only the badge toggles.
    await review.locator('.strip-toggle.collapse').click()
    await expect(review.locator('.strip-grouptile')).toHaveCount(1)
    await expect(review.locator('.strip-toggle.expand')).toHaveText(/2/)
    await review.locator('.strip-grouptile').click()
    await expect(review.locator('.strip-cluster')).toHaveCount(0)
    await review.locator('.strip-toggle.expand').click()
    await expect(review.locator('.strip-cluster')).toHaveCount(1)
    await expect(review.locator('.strip-cluster .strip-thumb')).toHaveCount(2)

    // "Ungroup" flattens to EVERY photo, the near-dup pair bound by a subtle
    // "group rail" so membership still reads (beach + beach2 + city = 3).
    await review.getByRole('button', { name: /Ungroup/ }).click()
    await expect(review.locator('.strip-grouptile')).toHaveCount(0)
    await expect(review.locator('.strip-cluster')).toHaveCount(0)
    await expect(review.locator('.strip-thumb')).toHaveCount(3)
    await expect(review.locator('.strip-flatgroup')).toHaveCount(1)
    await expect(review.locator('.strip-flatgroup .strip-thumb')).toHaveCount(2)

    await review.getByRole('button', { name: 'Close' }).click()
    await expect(review).toHaveCount(0)
  })

  test('arrow keys focus a scene pile and Enter opens its review', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'Scenes' }).click()
    const piles = page.locator('.scene-pile')
    await expect(piles).toHaveCount(2)

    // Scenes used to be mouse-only; they now share the grid keyboard nav with
    // the Groups view. Arrowing drives a roving focus ring across the piles.
    await page.locator('.grid-scroll').focus()
    await page.keyboard.press('ArrowRight')
    await expect(page.locator('.scene-pile.focused')).toHaveCount(1)

    // Enter opens the focused scene's review — the same overlay a click yields.
    await page.keyboard.press('Enter')
    const review = page.locator('.review-panel')
    await expect(review).toBeVisible()
    await expect(review).toContainText('Scene #')
    await review.getByRole('button', { name: 'Close' }).click()
    await expect(review).toHaveCount(0)
  })

  test('a scene with no near-dup sets still opens into an open image', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'Scenes' }).click()

    // The second scene (portrait + cat) has no near-duplicate set — it must
    // still open into the same hero+filmstrip, not a bare thumbnail grid.
    await page.locator('.scene-pile').nth(1).click()
    const review = page.locator('.review-panel')
    await expect(review).toBeVisible()
    await expect(review.locator('.review-hero img')).toBeVisible()
    await expect(review.locator('.strip-thumb').first()).toBeVisible()
    // No near-dup sets -> no cluster chrome and no "Group dups" affordance.
    await expect(review.locator('.strip-cluster')).toHaveCount(0)
    await expect(review.getByRole('button', { name: /Group dups/ })).toHaveCount(0)

    // Clicking the hero zooms into the full-size lightbox.
    await review.locator('.review-hero').click()
    await expect(page.locator('.lightbox')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.lightbox')).toHaveCount(0)
    await review.getByRole('button', { name: 'Close' }).click()
  })
})

test.describe('browser history + deep links', () => {
  test('lightbox: each photo is a Back step, then Back closes', async ({ page }) => {
    await page.goto('/')
    await cards(page).first().locator('.thumb-wrap').click()
    const lb = page.locator('.lightbox')
    await expect(lb).toBeVisible()
    await expect(page).toHaveURL(/[?&]img=\d+/)
    await expect(lb.locator('.lb-name')).toContainText('beach.jpg')

    // Arrowing to the next photo is its own history entry.
    await page.keyboard.press('ArrowRight')
    await expect(lb.locator('.lb-name')).toContainText('portrait.jpg')

    // Back steps to the previously viewed photo (lightbox still open)…
    await page.goBack()
    await expect(lb).toBeVisible()
    await expect(lb.locator('.lb-name')).toContainText('beach.jpg')

    // …and one more Back closes the lightbox entirely.
    await page.goBack()
    await expect(lb).toHaveCount(0)
    await expect(page).not.toHaveURL(/[?&]img=/)
  })

  test('lightbox: close button unwinds the whole viewer in one go', async ({ page }) => {
    await page.goto('/')
    await cards(page).first().locator('.thumb-wrap').click()
    const lb = page.locator('.lightbox')
    await expect(lb).toBeVisible()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('ArrowRight')
    // The × button exits, regardless of how many photos were stepped through.
    await lb.locator('.lb-close').click()
    await expect(lb).toHaveCount(0)
    await expect(page).not.toHaveURL(/[?&]img=/)
  })

  test('a shared lightbox link restores the open photo on load', async ({ page }) => {
    await page.goto('/')
    await cards(page).first().locator('.thumb-wrap').click()
    await expect(page.locator('.lightbox')).toBeVisible()
    const shared = page.url()
    expect(shared).toMatch(/[?&]img=\d+/)

    // Fresh load of the deep link reopens the same image.
    await page.goto(shared)
    const lb = page.locator('.lightbox')
    await expect(lb).toBeVisible()
    await expect(lb.locator('.lb-name')).toContainText('beach.jpg')
  })

  test('group review: open + focused image live in the URL', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: 'Groups' }).click()
    await page.locator('.pile').first().click()
    const panel = page.locator('.review-panel')
    await expect(panel).toBeVisible()
    await expect(page).toHaveURL(/[?&]grp=\d+/)

    // Picking a different strip thumb records the focused image in the URL.
    await panel.locator('.strip-thumb').nth(1).click()
    await expect(page).toHaveURL(/[?&]img=\d+/)

    // Back drops the focused image (panel stays), then closes the panel.
    await page.goBack()
    await expect(panel).toBeVisible()
    await expect(page).not.toHaveURL(/[?&]img=/)
    await page.goBack()
    await expect(panel).toHaveCount(0)
    await expect(page).not.toHaveURL(/[?&]grp=/)
  })

  test('Close exits a deep-linked review even after an in-overlay step', async ({ page }) => {
    // Grab a shareable review URL…
    await page.goto('/')
    await page.getByRole('button', { name: 'Groups' }).click()
    await page.locator('.pile').first().click()
    const panel = page.locator('.review-panel')
    await expect(panel).toBeVisible()
    const deepLink = page.url()
    expect(deepLink).toMatch(/[?&]grp=\d+/)

    // …then load straight into it (the review is now the first history entry,
    // with no plain-list entry beneath it) and take one in-overlay step.
    await page.goto(deepLink)
    await expect(panel).toBeVisible()
    await panel.locator('.strip-thumb').nth(1).click()
    await expect(page).toHaveURL(/[?&]img=\d+/)

    // Close must fully exit — this used to no-op because history.go had no
    // pre-overlay entry to unwind onto.
    await panel.getByRole('button', { name: 'Close' }).click()
    await expect(panel).toHaveCount(0)
    await expect(page).not.toHaveURL(/[?&]grp=/)
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
  test('move del-marked files to Trash and restore', async ({ page }) => {
    await page.goto('/')
    const first = cards(page).first()
    await decideAndWait(page, () => first.getByRole('button', { name: 'Delete' }).click())

    // Apply status is queried independently; reload so it sees the pending mark.
    await page.reload()
    const apply = page.locator('.apply-panel')
    await taskAndWait(page, () => apply.getByRole('button', { name: /Move \d+ to Trash/ }).click())
    await expect(apply.getByRole('button', { name: /Restore \(\d+ trashed\)/ })).toBeVisible()

    await taskAndWait(page, () => apply.getByRole('button', { name: /Restore \(\d+ trashed\)/ }).click())
    await expect(apply.getByRole('button', { name: /Move \d+ to Trash/ })).toBeVisible()

    // cleanup: clear the restored del mark.
    await decideAndWait(page, () => cards(page).first().getByRole('button', { name: 'Delete' }).click())
  })
})
