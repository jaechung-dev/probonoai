import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { HelmetProvider } from 'react-helmet-async'
import { describe, test, expect, beforeEach, vi } from 'vitest'
import OAuthConsentPage from '@/src/pages/OAuthConsentPage'
import { safeNext } from '@/lib/nextPath'

vi.mock('@/context/auth', () => ({ useAuth: vi.fn() }))
const { useAuth } = await import('@/context/auth')

const REDIRECT = 'https://chatgpt.com/aip/g-1/oauth/callback'
const QS = `?client_id=cid&redirect_uri=${encodeURIComponent(REDIRECT)}&scope=${encodeURIComponent('cases:read search')}&state=s1&code_challenge=abc&code_challenge_method=S256`

function renderPage(search = QS) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[`/oauth/consent${search}`]}><OAuthConsentPage /></MemoryRouter>
    </HelmetProvider>,
  )
}

describe('OAuthConsentPage', () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({
      user: { username: 'a@b.c', name: 'A', role: 'user', email_verified: true }, token: 'tok', loading: false,
    } as any)
  })

  test('shows read-only scopes in plain English and the return host', () => {
    renderPage()
    expect(screen.getByText(/read-only access/i)).toBeInTheDocument()
    expect(screen.getByText(/view your cases/i)).toBeInTheDocument()
    expect(screen.getByText(/chatgpt\.com/)).toBeInTheDocument()
  })

  test('rejects non-https redirect URIs', () => {
    renderPage(`?client_id=cid&redirect_uri=${encodeURIComponent('http://evil.example/cb')}`)
    expect(screen.getByText(/invalid/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /allow/i })).toBeNull()
  })

  test('Allow posts to the approve endpoint with the PKCE challenge', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ redirect_to: `${REDIRECT}?code=c&state=s1` }),
    })
    vi.stubGlobal('fetch', fetchMock)
    // jsdom: make location assignable
    Object.defineProperty(window, 'location', { value: { href: '' }, writable: true })
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /allow/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/oauth\/authorize\/approve$/)
    const body = JSON.parse(init.body)
    expect(body).toMatchObject({ client_id: 'cid', code_challenge: 'abc', code_challenge_method: 'S256', approve: true })
    expect(init.headers.Authorization).toBe('Bearer tok')
    await waitFor(() => expect(window.location.href).toContain('code=c'))
  })

  test('forwards the RFC 8707 resource parameter (MCP connector)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ redirect_to: `${REDIRECT}?code=c&state=s1` }),
    })
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(window, 'location', { value: { href: '' }, writable: true })
    renderPage(`${QS}&resource=${encodeURIComponent('https://api.probonoai.com.au/mcp')}`)
    fireEvent.click(screen.getByRole('button', { name: /allow/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).resource).toBe('https://api.probonoai.com.au/mcp')
  })

  test('refuses to follow a redirect to a different host', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ redirect_to: 'https://evil.example/cb?code=c' }),
    }))
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /allow/i }))
    await waitFor(() => expect(screen.getByText(/unexpected redirect/i)).toBeInTheDocument())
  })
})

describe('safeNext', () => {
  test('allows same-site paths only', () => {
    expect(safeNext('/oauth/consent?a=1')).toBe('/oauth/consent?a=1')
    expect(safeNext('//evil.com')).toBeNull()
    expect(safeNext('https://evil.com')).toBeNull()
    expect(safeNext('/\\evil.com')).toBeNull()
    expect(safeNext(null)).toBeNull()
  })
})
