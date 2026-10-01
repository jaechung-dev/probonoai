import { defineConfig, devices } from '@playwright/test'
import { readFileSync } from 'fs'
import { join } from 'path'

// Read the API URL from .env.production (committed, authoritative).
// An explicit env override (e.g. for a staging environment) takes precedence.
function getApiUrl(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL
  try {
    const env = readFileSync(join(__dirname, '.env.production'), 'utf-8')
    const m = env.match(/^NEXT_PUBLIC_API_URL=(.+)$/m)
    if (m) return m[1].trim()
  } catch { /* fall through */ }
  throw new Error('NEXT_PUBLIC_API_URL not found in .env.production — set it explicitly')
}

const API_URL = getApiUrl()
process.env.NEXT_PUBLIC_API_URL = API_URL
// MCP server — default to production unless overridden
if (!process.env.MCP_URL) process.env.MCP_URL = 'https://api.probonoai.com.au'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : 2,
  reporter: 'html',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'https://www.probonoai.com.au',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Only spin up a local preview server when PLAYWRIGHT_BASE_URL explicitly points to localhost
  webServer: process.env.PLAYWRIGHT_BASE_URL?.includes('localhost') ? {
    command: 'npm run preview',
    url: process.env.PLAYWRIGHT_BASE_URL,
    reuseExistingServer: true,
    timeout: 10000,
  } : undefined,
})
