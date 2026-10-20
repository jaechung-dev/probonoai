import { test } from '@playwright/test'

/**
 * Login for specs that need a real account. Never hardcode these: set
 * E2E_EMAIL / E2E_PASSWORD in the shell or in frontend/.env.local (gitignored).
 * Specs that need them are skipped when they are missing.
 */
export function e2eCredentials(): { email: string; password: string } {
  const email = process.env.E2E_EMAIL
  const password = process.env.E2E_PASSWORD
  test.skip(!email || !password, 'Set E2E_EMAIL and E2E_PASSWORD to run this spec')
  return { email: email!, password: password! }
}
