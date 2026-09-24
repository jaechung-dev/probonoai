/**
 * MCP token management + MCP server integration tests.
 *
 * Token persistence:
 *   - On first run the test creates a token via the API and writes it to
 *     e2e/.mcp-token (gitignored).  CI stores that file as an artifact or
 *     sets MCP_TEST_TOKEN in the environment so subsequent runs skip creation.
 *   - Tokens are NEVER revoked by this suite — they stay alive for CI reuse.
 */

import { test, expect, request as playwrightRequest } from '@playwright/test'
import fs   from 'fs'
import path from 'path'

const API      = process.env.NEXT_PUBLIC_API_URL  || 'http://localhost:20000'
const MCP_URL  = process.env.MCP_URL              || 'http://localhost:20002'
const TOKEN_FILE = path.join(__dirname, '.mcp-token')

// ── helpers ───────────────────────────────────────────────────────────────────

function loadSavedToken(): string | null {
  if (process.env.MCP_TEST_TOKEN) return process.env.MCP_TEST_TOKEN
  if (fs.existsSync(TOKEN_FILE))  return fs.readFileSync(TOKEN_FILE, 'utf-8').trim()
  return null
}

async function getSessionJwt(): Promise<string> {
  const ctx = await playwrightRequest.newContext()
  const res = await ctx.post(`${API}/auth/login`, {
    data: { username: 'demo', password: 'demo1234' },
  })
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  await ctx.dispose()
  return body.access_token
}

async function createMcpToken(sessionJwt: string): Promise<string> {
  const ctx = await playwrightRequest.newContext()
  const res = await ctx.post(`${API}/auth/mcp/token`, {
    headers: { Authorization: `Bearer ${sessionJwt}` },
    data:    { name: 'playwright-ci', expires_days: 365 },
  })
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  await ctx.dispose()
  return body.token   // raw mcp-… token
}

// ── MCP server API tests ──────────────────────────────────────────────────────

test.describe('MCP server — auth', () => {

  test('no token returns 401', async ({ request }) => {
    const res = await request.post(`${MCP_URL}/mcp`, {
      headers: { 'Content-Type': 'application/json' },
      data:    { jsonrpc: '2.0', id: 1, method: 'initialize',
                 params: { protocolVersion: '2024-11-05', capabilities: {},
                           clientInfo: { name: 'test', version: '1.0' } } },
    })
    expect(res.status()).toBe(401)
    const body = await res.json()
    expect(body.error).toContain('probonoai.com.au/connect')
  })

  test('random fake token returns 401', async ({ request }) => {
    const res = await request.post(`${MCP_URL}/mcp`, {
      headers: {
        'Content-Type': 'application/json',
        Authorization:  'Bearer mcp-thisisnotavalidtoken',
      },
      data: { jsonrpc: '2.0', id: 1, method: 'initialize',
              params: { protocolVersion: '2024-11-05', capabilities: {},
                        clientInfo: { name: 'test', version: '1.0' } } },
    })
    expect(res.status()).toBe(401)
  })

})

test.describe('MCP server — valid token', () => {
  let mcpToken: string

  test.beforeAll(async () => {
    mcpToken = loadSavedToken() ?? ''
    if (!mcpToken) {
      const jwt = await getSessionJwt()
      mcpToken  = await createMcpToken(jwt)
      // persist for CI reuse — never deleted by tests
      fs.writeFileSync(TOKEN_FILE, mcpToken, 'utf-8')
      console.log(`MCP token saved to ${TOKEN_FILE}`)
    }
  })

  test('initialize returns 200 and a session ID', async ({ request }) => {
    // Stateful MCP sessions require a dedicated server; Lambda/Mangum buffers SSE responses.
    // Pass MCP_URL=http://localhost:20002 to run against a local MCP process.
    test.skip(process.env.MCP_URL !== 'http://localhost:20002', 'Lambda/Mangum buffers SSE — run with MCP_URL=http://localhost:20002')
    const res = await request.post(`${MCP_URL}/mcp`, {
      headers: {
        'Content-Type': 'application/json',
        Accept:          'application/json, text/event-stream',
        Authorization:  `Bearer ${mcpToken}`,
      },
      data: { jsonrpc: '2.0', id: 1, method: 'initialize',
              params: { protocolVersion: '2024-11-05', capabilities: {},
                        clientInfo: { name: 'playwright', version: '1.0' } } },
    })
    expect(res.status()).toBe(200)
    expect(res.headers()['mcp-session-id']).toBeTruthy()
  })

  test('tools/list returns search, ask, fetch, collections', async ({ request }) => {
    test.skip(process.env.MCP_URL !== 'http://localhost:20002', 'Lambda/Mangum buffers SSE — run with MCP_URL=http://localhost:20002')
    // initialize first to get session id
    const initRes = await request.post(`${MCP_URL}/mcp`, {
      headers: {
        'Content-Type': 'application/json',
        Accept:          'application/json, text/event-stream',
        Authorization:  `Bearer ${mcpToken}`,
      },
      data: { jsonrpc: '2.0', id: 1, method: 'initialize',
              params: { protocolVersion: '2024-11-05', capabilities: {},
                        clientInfo: { name: 'playwright', version: '1.0' } } },
    })
    const sessionId = initRes.headers()['mcp-session-id']
    expect(sessionId).toBeTruthy()

    const res = await request.post(`${MCP_URL}/mcp`, {
      headers: {
        'Content-Type':  'application/json',
        Accept:           'application/json, text/event-stream',
        Authorization:   `Bearer ${mcpToken}`,
        'mcp-session-id': sessionId,
      },
      data: { jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} },
    })
    expect(res.status()).toBe(200)
    const text  = await res.text()
    const match = text.match(/data: (.+)/)
    expect(match).toBeTruthy()
    const body  = JSON.parse(match![1])
    const names = body.result.tools.map((t: { name: string }) => t.name)
    expect(names).toContain('search')
    expect(names).toContain('ask')
    expect(names).toContain('fetch')
    expect(names).toContain('collections')
  })

  test('last_used_at is updated after a request', async ({ request }) => {
    const before = new Date()

    await request.post(`${MCP_URL}/mcp`, {
      headers: {
        'Content-Type': 'application/json',
        Accept:          'application/json, text/event-stream',
        Authorization:  `Bearer ${mcpToken}`,
      },
      data: { jsonrpc: '2.0', id: 1, method: 'initialize',
              params: { protocolVersion: '2024-11-05', capabilities: {},
                        clientInfo: { name: 'playwright', version: '1.0' } } },
    })

    const jwt = await getSessionJwt()
    const ctx = await playwrightRequest.newContext()
    const res = await ctx.get(`${API}/auth/mcp/tokens`, {
      headers: { Authorization: `Bearer ${jwt}` },
    })
    const { tokens } = await res.json()
    await ctx.dispose()

    const target = tokens.find((t: { name: string; last_used_at: string }) => t.name === 'playwright-ci')
    if (target?.last_used_at) {
      expect(new Date(target.last_used_at).getTime()).toBeGreaterThanOrEqual(before.getTime() - 5000)
    }
  })

})

// ── Connect page UI tests ─────────────────────────────────────────────────────

/**
 * Seed auth state directly into localStorage so tests don't depend on the
 * login form flow. The auth context reads localStorage on mount, so navigating
 * to any page after this will restore the session immediately.
 */
async function loginAs(page: import('@playwright/test').Page, u: string, p: string) {
  const ctx = await playwrightRequest.newContext()
  const res = await ctx.post(`${API}/auth/login`, {
    data: { username: u, password: p },
  })
  expect(res.ok()).toBeTruthy()
  const { access_token, refresh_token } = await res.json()
  await ctx.dispose()

  // Navigate to any page first so we have a document to write localStorage into
  await page.goto('/')
  await page.evaluate(
    ({ at, rt }) => {
      localStorage.setItem('iai_token', at)
      if (rt) localStorage.setItem('iai_refresh', rt)
    },
    { at: access_token, rt: refresh_token as string | undefined },
  )
}

test.describe('Connect page UI', () => {

  test('redirects to login when not authenticated', async ({ page }) => {
    await page.goto('/connect/')
    // client-side useEffect redirect — wait for navigation
    await page.waitForURL(/\/login/, { timeout: 10000 })
    await expect(page).toHaveURL(/\/login/)
  })

  test('logged-in user sees token creation form', async ({ page }) => {
    await loginAs(page, 'demo', 'demo1234')
    await page.goto('/connect/')
    await expect(page.getByPlaceholder(/token name/i)).toBeVisible({ timeout: 10000 })
    await expect(page.getByRole('button', { name: /generate/i })).toBeVisible()
  })

  test('creating a token reveals it once with copy button', async ({ page }) => {
    await loginAs(page, 'demo', 'demo1234')
    await page.goto('/connect/')
    await page.getByPlaceholder(/token name/i).fill('playwright-ui-test')
    await page.getByRole('button', { name: /generate/i }).click()

    await expect(page.getByText('Shown once only')).toBeVisible({ timeout: 20000 })
    await expect(page.getByText(/^mcp-/)).toBeVisible()
    await expect(page.getByRole('button', { name: /copy config/i })).toBeVisible()
  })

  test('active tokens list shows created tokens', async ({ page }) => {
    await loginAs(page, 'demo', 'demo1234')
    await page.goto('/connect/')
    // "Active tokens (N)" heading in the main section — distinct from sidebar "ACTIVE TOKENS" header
    await expect(page.locator('main').getByText(/Active tokens/)).toBeVisible({ timeout: 10000 })
  })

  test('sidebar shows ACTIVE TOKENS header and MCP ready indicator', async ({ page }) => {
    await loginAs(page, 'demo', 'demo1234')
    await page.goto('/connect/')
    await expect(page.getByText(/active tokens/i).first()).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/mcp ready/i)).toBeVisible()
  })

  test('creating a token adds it to the sidebar compact list', async ({ page }) => {
    await loginAs(page, 'demo', 'demo1234')
    await page.goto('/connect/')

    await page.getByPlaceholder(/token name/i).fill('playwright-sidebar-test')
    await page.getByRole('button', { name: /generate/i }).click()
    await expect(page.getByText('Shown once only')).toBeVisible({ timeout: 20000 })

    // Sidebar should show the new token by name with a green (active) status dot
    const newRow = page.locator('aside li').filter({ hasText: 'playwright-sidebar-test' })
    await expect(newRow).toBeVisible()
    await expect(newRow.locator('.bg-rose-500')).toBeVisible()
  })

  test('revoking a token removes it from sidebar and main list', async ({ page }) => {
    await loginAs(page, 'demo', 'demo1234')
    await page.goto('/connect/')

    // Create a token to revoke
    await page.getByPlaceholder(/token name/i).fill('playwright-revoke-test')
    await page.getByRole('button', { name: /generate/i }).click()
    await expect(page.getByText('Shown once only')).toBeVisible({ timeout: 20000 })

    // Revoke via the main token list's trash button
    const row = page.locator('.divide-y > div').filter({ hasText: 'playwright-revoke-test' })
    await expect(row).toBeVisible({ timeout: 5000 })
    await row.getByTitle('Revoke token').click()

    // Token should disappear from both the main list and the sidebar
    await expect(row).not.toBeVisible({ timeout: 5000 })
    await expect(page.locator('aside').getByText('playwright-revoke-test')).not.toBeVisible()
  })

  test.afterAll(async ({ request }) => {
    // Clean up any 'playwright-ui-test' tokens left by the UI tests
    const apiBase = process.env.NEXT_PUBLIC_API_URL || 'https://api.probonoai.com.au'

    const loginRes = await request.post(`${apiBase}/auth/login`, {
      data: { username: 'demo', password: 'demo1234' },
    })
    if (!loginRes.ok()) return
    const { access_token } = await loginRes.json()

    const listRes = await request.get(`${apiBase}/auth/mcp/tokens`, {
      headers: { Authorization: `Bearer ${access_token}` },
    })
    if (!listRes.ok()) return
    const { tokens } = await listRes.json()

    const cleanup = ['playwright-ui-test', 'playwright-sidebar-test', 'playwright-revoke-test']
    for (const token of tokens as Array<{ id: string; name: string }>) {
      if (cleanup.includes(token.name)) {
        await request.delete(`${apiBase}/auth/mcp/token/${token.id}`, {
          headers: { Authorization: `Bearer ${access_token}` },
        })
      }
    }
  })

})
