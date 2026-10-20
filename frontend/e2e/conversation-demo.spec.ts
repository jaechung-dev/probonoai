/**
 * Visual demo test for conversation history feature — Bella's account.
 * Run: npx playwright test conversation-demo
 */
import { test, expect, request as playwrightRequest } from '@playwright/test'
import { e2eCredentials } from './credentials'

const API   = process.env.NEXT_PUBLIC_API_URL || 'https://api.probonoai.com.au'
const QUESTION = 'Who sent threatening messages and what did they say?'
const SS    = (name: string) => `screenshots/${name}.png`

async function loginAsBella(page: import('@playwright/test').Page) {
  const { email, password } = e2eCredentials()
  const ctx = await playwrightRequest.newContext()
  const res = await ctx.post(`${API}/auth/login`, {
    data: { username: email, password },
  })
  const { access_token, refresh_token } = await res.json()
  await ctx.dispose()
  await page.goto('/')
  await page.evaluate(({ at, rt }) => {
    localStorage.setItem('iai_token', at)
    if (rt) localStorage.setItem('iai_refresh', rt)
  }, { at: access_token, rt: refresh_token ?? null })
}

test('conversation sidebar — Bella', async ({ page }) => {
  // Login + a full /chat answer (buffered by API Gateway, ~15–25s) plus the
  // 90s/30s waits below don't fit Playwright's default 30s test timeout.
  test.setTimeout(150_000)
  const apiCalls: string[] = []
  page.on('response', async (resp) => {
    if (resp.url().includes('execute-api')) {
      const snippet = `${resp.request().method()} ${resp.url().split('/').slice(-2).join('/')} → ${resp.status()}`
      apiCalls.push(snippet)
      console.log('[api]', snippet)
    }
  })

  // 1. Login and open chat
  await loginAsBella(page)
  await page.goto('/chat/')
  await page.waitForTimeout(2000)
  await page.screenshot({ path: SS('01-chat-logged-in'), fullPage: false })

  // 2. Type a question and register network listener BEFORE pressing Enter.
  // POST /conversations fires immediately at the start of sendMessage (before
  // the SSE stream), so waitForResponse must be set up first or it races.
  const input = page.getByPlaceholder(/ask a question about nsw law/i)
  await expect(input).toBeVisible({ timeout: 5000 })
  await input.fill(QUESTION)
  await page.screenshot({ path: SS('02-message-typed'), fullPage: false })

  const convCreated = page.waitForResponse(
    r => r.url().includes('/conversations') && r.request().method() === 'POST' && r.status() === 201,
    { timeout: 20000 },
  )
  await input.press('Enter')

  // 3. Wait for the user bubble in the CHAT AREA specifically (not sidebar text)
  const userBubble = page.locator('div.bg-gray-900.text-white').filter({ hasText: QUESTION.slice(0, 20) })
  await expect(userBubble).toBeVisible({ timeout: 10000 })
  await page.screenshot({ path: SS('03-user-message-sent'), fullPage: false })

  // 4. Wait for the NEW conversation to appear at the top of the sidebar.
  await convCreated
  const convItem = page.locator('aside ul li button').first()
  await expect(convItem).toBeVisible({ timeout: 5000 })
  await page.screenshot({ path: SS('04-conversation-in-sidebar'), fullPage: false })

  // 5. Wait for assistant response bubble to contain text
  const assistantBubble = page.locator('div.bg-gray-50.text-gray-800').first()
  await expect(assistantBubble).toBeVisible({ timeout: 90000 })
  await expect(assistantBubble).not.toBeEmpty()
  await expect(input).toBeEnabled({ timeout: 30000 })
  await page.waitForTimeout(2500)
  await page.screenshot({ path: SS('05-response-complete'), fullPage: false })

  // 6. Start new chat
  await page.getByRole('button', { name: /new chat/i }).click()
  await page.waitForTimeout(800)
  await page.screenshot({ path: SS('06-new-chat-cleared'), fullPage: false })

  // 7. Click the first sidebar item to restore the conversation
  await convItem.click()
  await page.waitForTimeout(1500)
  await page.screenshot({ path: SS('07-conversation-restored'), fullPage: false })

  console.log('[api calls summary]', apiCalls)
})
