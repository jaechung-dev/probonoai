import { test, expect } from '@playwright/test'

test('existing session survives access-token expiry via refresh cookie', async ({ page }) => {
  // 1. Login normally
  await page.goto('/login/')
  await page.getByPlaceholder(/username/i).fill('demo')
  await page.getByPlaceholder(/••••••••/).fill('demo1234')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page).toHaveURL(/\/chat/, { timeout: 10000 })

  // 2. Confirm session flag and access token are set
  const hasSession = await page.evaluate(() => localStorage.getItem('iai_has_session'))
  expect(hasSession).toBe('1')

  // 3. Simulate access token expiry: remove the token but leave iai_has_session intact
  await page.evaluate(() => localStorage.removeItem('iai_token'))

  // 4. Hard reload — wait for /auth/refresh network call to complete
  const [refreshResp] = await Promise.all([
    page.waitForResponse(r => r.url().includes('/auth/refresh'), { timeout: 10000 }),
    page.reload(),
  ])
  expect(refreshResp.status()).toBe(200)

  // 5. Should still be on a protected page, not redirected to /login
  await expect(page).not.toHaveURL(/\/login/, { timeout: 10000 })
  await expect(page).toHaveURL(/\/chat/, { timeout: 10000 })

  // 6. Access token should be back in localStorage (refresh succeeded)
  const newToken = await page.evaluate(() => localStorage.getItem('iai_token'))
  expect(newToken).toBeTruthy()
})

test('/connect page loads without error', async ({ page }) => {
  await page.goto('/connect/')
  await expect(page).not.toHaveURL(/\/login/)
  await expect(page.locator('body')).not.toContainText('500')
})

test('/oauth/consent without params shows invalid request when logged in', async ({ page }) => {
  // Must be logged in — unauthenticated requests redirect to /login
  await page.goto('/login/')
  await page.getByPlaceholder(/username/i).fill('demo')
  await page.getByPlaceholder(/••••••••/).fill('demo1234')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page).toHaveURL(/\/chat/, { timeout: 10000 })

  await page.goto('/oauth/consent/')
  await expect(page.getByText(/connection request is invalid/i)).toBeVisible({ timeout: 5000 })
})
