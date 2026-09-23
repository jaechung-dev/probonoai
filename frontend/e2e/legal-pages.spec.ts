import { test, expect } from '@playwright/test'

// ── Privacy Policy ────────────────────────────────────────────────────────────

test('privacy page loads and shows heading', async ({ page }) => {
  await page.goto('/privacy/')
  await expect(page.getByRole('heading', { name: /privacy policy/i })).toBeVisible()
})

test('privacy page shows key sections', async ({ page }) => {
  await page.goto('/privacy/')
  await expect(page.getByText(/what we collect/i)).toBeVisible()
  await expect(page.getByText(/how we use your information/i)).toBeVisible()
  await expect(page.getByRole('heading', { name: /your rights/i })).toBeVisible()
  await expect(page.getByText(/australian privacy act/i).first()).toBeVisible()
})

test('privacy page has back link to home', async ({ page }) => {
  await page.goto('/privacy/')
  await expect(page.getByRole('link', { name: /back/i })).toBeVisible()
})

test('privacy page links to terms', async ({ page }) => {
  await page.goto('/privacy/')
  const link = page.getByRole('link', { name: /terms/i }).first()
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/\/terms/)
})

// ── Terms of Service ──────────────────────────────────────────────────────────

test('terms page loads and shows heading', async ({ page }) => {
  await page.goto('/terms/')
  await expect(page.getByRole('heading', { name: /terms of service/i })).toBeVisible()
})

test('terms page shows not-legal-advice banner', async ({ page }) => {
  await page.goto('/terms/')
  await expect(page.getByText(/not legal advice/i)).toBeVisible()
})

test('terms page shows key sections', async ({ page }) => {
  await page.goto('/terms/')
  await expect(page.getByText(/acceptable use/i)).toBeVisible()
  await expect(page.getByText(/limitation of liability/i)).toBeVisible()
  await expect(page.getByText(/governing law/i)).toBeVisible()
})

test('terms page links to privacy', async ({ page }) => {
  await page.goto('/terms/')
  const link = page.getByRole('link', { name: /privacy/i }).first()
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/\/privacy/)
})

// ── Homepage footer ───────────────────────────────────────────────────────────

test('homepage footer has Privacy link', async ({ page }) => {
  await page.goto('/')
  const link = page.getByRole('link', { name: /^privacy$/i })
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/\/privacy/)
})

test('homepage footer has Terms link', async ({ page }) => {
  await page.goto('/')
  const link = page.getByRole('link', { name: /^terms$/i })
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/\/terms/)
})

test('homepage footer shows copyright year', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText(/© 2026/i)).toBeVisible()
})

// ── Register page ToS links navigate correctly ────────────────────────────────

test('ToS link in register form opens terms page', async ({ page }) => {
  await page.goto('/register/')
  const [newPage] = await Promise.all([
    page.context().waitForEvent('page'),
    page.getByRole('link', { name: /terms of service/i }).click(),
  ])
  await expect(newPage).toHaveURL(/\/terms/)
  await newPage.close()
})

test('Privacy link in register form opens privacy page', async ({ page }) => {
  await page.goto('/register/')
  const [newPage] = await Promise.all([
    page.context().waitForEvent('page'),
    page.getByRole('link', { name: /privacy policy/i }).click(),
  ])
  await expect(newPage).toHaveURL(/\/privacy/)
  await newPage.close()
})
