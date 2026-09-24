import { test, expect } from '@playwright/test'

// NOTE: The timeline view (TimelineClient.tsx) was extracted from the home page when
// the landing page was added. These tests now cover the landing page at /.
// Timeline-specific tests should be re-added once the timeline has its own route.

test('landing page shows hero content', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /legal help in/i })).toBeVisible({ timeout: 10000 })
  await expect(page.getByText(/plain english/i).first()).toBeVisible()
})

test('landing page has Get started and Sign in buttons', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: /create free account/i })).toBeVisible()
  // Landing page has Sign in in both header and hero — assert at least one is visible
  await expect(page.getByRole('button', { name: /sign in/i }).first()).toBeVisible()
})

test('landing page clicking Sign in opens login modal', async ({ page }) => {
  await page.goto('/')
  // Click the Sign in button (appears in header and hero)
  await page.getByRole('button', { name: /sign in/i }).first().click()
  await expect(page.getByRole('heading', { name: /2 free messages/i })).toBeVisible()
  await expect(page.getByPlaceholder(/username/i)).toBeVisible()
})

test('landing page shows feature list', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText(/NSW legislation & caselaw search/i)).toBeVisible()
  await expect(page.getByText(/plain english answers/i)).toBeVisible()
})
