/** Validate a post-login return path: same-site, absolute path only (no open redirects). */
export function safeNext(raw: string | null | undefined): string | null {
  if (!raw) return null
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return null
  if (/[\r\n]/.test(raw)) return null
  return raw
}

const KEY = 'iai_next'

/** Remember the return path across the Google sign-in round trip. */
export function rememberNext(path: string | null): void {
  try { if (path) sessionStorage.setItem(KEY, path) } catch { /* ignore */ }
}

export function takeNext(): string | null {
  try {
    const v = sessionStorage.getItem(KEY)
    sessionStorage.removeItem(KEY)
    return safeNext(v)
  } catch { return null }
}
