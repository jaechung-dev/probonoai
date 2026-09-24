import { test, expect } from '@playwright/test'
import path from 'path'

const TEST_PDF = path.join(__dirname, 'fixtures', 'test.pdf')

async function loginAsDemo(page: any) {
  await page.goto('/login/')
  await page.getByPlaceholder(/username/i).fill('demo')
  await page.getByPlaceholder(/••••••••/).fill('demo1234')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page).toHaveURL(/\/chat/, { timeout: 10000 })
}

test('My Case page loads with sidebar sections', async ({ page }) => {
  await loginAsDemo(page)
  await page.goto('/my-case/')
  await expect(page.locator('p').filter({ hasText: /^My Case$/i }).first()).toBeVisible({ timeout: 10000 })
  await expect(page.getByRole('button', { name: /Manage Documents/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /Manage Cases/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /Timeline/i })).toBeVisible()
})

test('upload PDF to existing case and see it in document list', async ({ page }) => {
  await loginAsDemo(page)
  await page.goto('/my-case/')

  // Wait for cases to load (sidebar active case label appears)
  await expect(page.getByRole('button', { name: /Manage Documents/i })).toBeVisible({ timeout: 10000 })

  // Navigate to Manage Documents section
  await page.getByRole('button', { name: /Manage Documents/i }).click()
  await expect(page.getByRole('heading', { name: /Manage Documents/i })).toBeVisible()

  // Set the hidden file input directly (bypasses the click-to-open dialog)
  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles(TEST_PDF)

  // File should appear in the list (either uploading spinner or completed)
  await expect(page.getByText('test.pdf')).toBeVisible({ timeout: 15000 })
})

test('New case button in sidebar links to intake step 2', async ({ page }) => {
  await loginAsDemo(page)
  await page.goto('/my-case/')

  const newCaseLink = page.getByRole('link', { name: /new case/i })
  await expect(newCaseLink).toBeVisible({ timeout: 10000 })
  await expect(newCaseLink).toHaveAttribute('href', '/intake?step=2')
})

test('Manage Cases section lists existing cases', async ({ page }) => {
  await loginAsDemo(page)
  await page.goto('/my-case/')

  await page.getByRole('button', { name: /Manage Cases/i }).click()
  // Should show at least one case row or the new case prompt
  await expect(
    page.locator('text=/active|bail|family|criminal|New case/i').first()
  ).toBeVisible({ timeout: 10000 })
})
