import { test, expect } from '@playwright/test'
import path from 'path'
import { e2eCredentials } from './credentials'

const COURT_EML = path.join(__dirname, 'fixtures', 'court_notice.eml')
const COURT_PDF = path.join(__dirname, 'fixtures', 'court_schedule.pdf')

async function login(page: any) {
  const { email, password } = e2eCredentials()
  await page.goto('/login/')
  await page.getByPlaceholder(/username/i).fill(email)
  await page.getByPlaceholder(/••••••••/).fill(password)
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page).toHaveURL(/\/chat/, { timeout: 10000 })
}

test.describe('timeline upload and view', () => {
  test('upload court notice email and court schedule PDF, then view timeline events', async ({ page }) => {
    test.setTimeout(120_000)

    await login(page)
    await page.goto('/my-case/')

    // Open Manage Documents section
    await page.getByRole('button', { name: /Manage Documents/i }).click()
    await expect(page.getByRole('heading', { name: /Manage Documents/i })).toBeVisible({ timeout: 10000 })

    const fileInput = page.locator('input[type="file"]')

    // Upload court notice email
    await fileInput.setInputFiles(COURT_EML)
    await expect(page.getByText('court_notice.eml')).toBeVisible({ timeout: 15000 })

    // Upload court schedule PDF
    await fileInput.setInputFiles(COURT_PDF)
    await expect(page.getByText('court_schedule.pdf')).toBeVisible({ timeout: 15000 })

    // Wait for Lambda to process both files (embeddings + timeline extraction)
    // Files show a spinner while processing; wait until both show as ready
    await expect(page.locator('[data-testid="file-status-ready"]').nth(1)).toBeVisible({ timeout: 60_000 })
      .catch(() => {
        // file-status-ready may not be implemented — just wait a fixed time for Lambda
        return page.waitForTimeout(30_000)
      })

    // Navigate to Timeline
    await page.getByRole('button', { name: /Timeline/i }).click()
    await expect(page.getByRole('heading', { name: /Timeline/i })).toBeVisible({ timeout: 10000 })

    // At least one timeline event should appear (Lambda extracted dates from both docs)
    const timelineRows = page.locator('[data-testid="timeline-row"]')
    await expect(timelineRows.first()).toBeVisible({ timeout: 40_000 })

    const count = await timelineRows.count()
    expect(count).toBeGreaterThanOrEqual(1)

    // Screenshot: timeline with events visible
    await page.screenshot({ path: path.join(__dirname, 'screenshots', 'timeline-events.png'), fullPage: false })

    // Click the first event card — modal should open with content
    await timelineRows.first().click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible({ timeout: 5000 })

    // Modal should show a title and non-empty content block
    const title = dialog.locator('[class*="DialogTitle"]').or(dialog.getByRole('heading'))
    await expect(title.first()).toBeVisible()

    const contentBlock = dialog.locator('p').filter({ hasText: /\S/ }).first()
    await expect(contentBlock).toBeVisible()

    // Screenshot: modal open
    await page.screenshot({ path: path.join(__dirname, 'screenshots', 'timeline-modal.png'), fullPage: false })

    // Close modal
    await page.keyboard.press('Escape')
    await expect(dialog).not.toBeVisible({ timeout: 3000 })
  })

  test('timeline filter chips only show present categories', async ({ page }) => {
    test.setTimeout(30_000)

    await login(page)
    await page.goto('/my-case/')

    await page.getByRole('button', { name: /Timeline/i }).click()
    await expect(page.getByRole('heading', { name: /Timeline/i })).toBeVisible({ timeout: 10000 })

    // If there are events, only their categories appear as chips
    const timelineRows = page.locator('[data-testid="timeline-row"]')
    const hasEvents = await timelineRows.first().isVisible({ timeout: 5000 }).catch(() => false)

    if (hasEvents) {
      // "All" chip is always present
      await expect(page.getByRole('button', { name: /^All$/ })).toBeVisible()

      // Extinct categories like "Verdict" should NOT appear if no Verdict events exist
      const verdictChip = page.getByRole('button', { name: /^Verdict/ })
      const verdictVisible = await verdictChip.isVisible().catch(() => false)
      if (verdictVisible) {
        // If it is visible, there must be a matching event
        const verdictEvents = await timelineRows.filter({ hasText: /verdict/i }).count()
        expect(verdictEvents).toBeGreaterThan(0)
      }
    }
  })
})
