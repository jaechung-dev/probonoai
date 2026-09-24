import { test, expect } from '@playwright/test'

// ── Login ──────────────────────────────────────────────────────────────────────

test('login with demo credentials redirects to /chat/', async ({ page }) => {
  await page.goto('/login/')
  await page.getByPlaceholder(/username/i).fill('demo')
  await page.getByPlaceholder(/••••••••/).fill('demo1234')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page).toHaveURL(/\/chat/, { timeout: 10000 })
})

test('login with invalid credentials shows error', async ({ page }) => {
  await page.goto('/login/')
  await page.getByPlaceholder(/username/i).fill('nobody@example.com')
  await page.getByPlaceholder(/••••••••/).fill('wrongpass')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByText(/invalid/i)).toBeVisible({ timeout: 10000 })
})

test('login page has forgot password link', async ({ page }) => {
  await page.goto('/login/')
  const link = page.getByRole('link', { name: /forgot password/i })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', '/forgot-password')
})

test('login page banner shows verified message when ?verified=1', async ({ page }) => {
  await page.goto('/login/?verified=1')
  await expect(page.getByText(/email verified/i)).toBeVisible()
})

test('login page banner shows reset message when ?reset=1', async ({ page }) => {
  await page.goto('/login/?reset=1')
  await expect(page.getByText(/password.*reset/i)).toBeVisible()
})

test('demo account autofill button fills credentials', async ({ page }) => {
  await page.goto('/login/')
  await page.getByRole('button', { name: /guest/i }).click()
  await expect(page.getByPlaceholder(/username/i)).toHaveValue('demo')
})

// ── Logout ─────────────────────────────────────────────────────────────────────

test('after login, logout returns to landing page', async ({ page }) => {
  await page.goto('/login/')
  await page.getByPlaceholder(/username/i).fill('demo')
  await page.getByPlaceholder(/••••••••/).fill('demo1234')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page).toHaveURL(/\/chat/, { timeout: 10000 })

  // Find and click logout in Nav
  await page.getByRole('button', { name: /sign out/i }).click()
  await expect(page).toHaveURL(/\/$|\/login/, { timeout: 15000 })
})

// ── Guest quota gate ───────────────────────────────────────────────────────────

test('guest quota gate shows login modal after 2 questions', async ({ page }) => {
  await page.goto('/chat/')

  // Simulate having used up the 2-question free allowance
  await page.evaluate(() => localStorage.setItem('iai_anon_count', '2'))

  const input = page.getByPlaceholder(/ask a question about nsw law/i)
  await input.fill('What is bail?')
  await input.press('Enter')

  // Login gate (modal) should appear
  await expect(page.getByText(/2 free messages/i)).toBeVisible({ timeout: 5000 })
})

test('guest can send first 2 questions without login', async ({ page }) => {
  await page.goto('/chat/')
  await page.evaluate(() => localStorage.removeItem('iai_anon_count'))

  const input = page.getByPlaceholder(/ask a question about nsw law/i)
  await input.fill('What is bail?')
  await input.press('Enter')

  // Message appears in thread (no gate)
  await expect(page.getByText('What is bail?')).toBeVisible()
  await expect(page.getByRole('heading', { name: /sign in/i })).not.toBeVisible()
})

// ── Forgot password ────────────────────────────────────────────────────────────

test('forgot password page renders email form', async ({ page }) => {
  await page.goto('/forgot-password/')
  await expect(page.getByRole('heading', { name: /forgot/i })).toBeVisible()
  await expect(page.getByPlaceholder(/you@example.com/i)).toBeVisible()
  await expect(page.getByRole('button', { name: /send reset/i })).toBeVisible()
})

test('forgot password submission shows check email message', async ({ page }) => {
  await page.goto('/forgot-password/')
  await page.getByPlaceholder(/you@example.com/i).fill('any@example.com')
  await page.getByRole('button', { name: /send reset/i }).click()
  await expect(page.getByText(/on its way|reset link/i)).toBeVisible({ timeout: 5000 })
})

// ── Reset password ─────────────────────────────────────────────────────────────

test('reset password page with no token shows invalid link error', async ({ page }) => {
  await page.goto('/reset-password/')
  await expect(page.getByText(/invalid reset link/i)).toBeVisible({ timeout: 5000 })
})

test('reset password page with bad token shows error after submit', async ({ page }) => {
  await page.goto('/reset-password/?token=badtoken123')
  await expect(page.getByPlaceholder(/min.*8 characters/i)).toBeVisible({ timeout: 5000 })
  await page.getByPlaceholder(/min.*8 characters/i).fill('NewPass123!')
  await page.getByPlaceholder(/repeat your password/i).fill('NewPass123!')
  await page.getByRole('button', { name: /set new password/i }).click()
  await expect(page.getByText(/invalid|expired/i)).toBeVisible({ timeout: 5000 })
})

// ── www landing page ───────────────────────────────────────────────────────────

test('landing page shows ProBono AI branding', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('ProBono AI').first()).toBeVisible()
  await expect(page.getByText(/not legal advice/i)).toBeVisible()
})
