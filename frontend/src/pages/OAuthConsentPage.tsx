import { useState } from 'react'
import { useEffect } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Helmet } from 'react-helmet-async'
import { Scale, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/context/auth'
import { API_URL as API, APP_NAME } from '@/lib/config'

// Plain-English labels. Only read-only scopes can ever be granted (the server rejects the rest).
const SCOPE_LABELS: Record<string, string> = {
  'cases:read':         'View your cases and case timelines',
  'conversations:read': 'View your saved conversations',
  search:               'Search NSW legislation and caselaw for you',
  ask:                  'Ask legal research questions on your behalf',
  timeline:             'View your case timeline',
}

export default function OAuthConsentPage() {
  const { user, token, loading } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const clientId     = params.get('client_id') || ''
  const redirectUri  = params.get('redirect_uri') || ''
  const state        = params.get('state') || ''
  const scope        = params.get('scope') || ''
  const challenge    = params.get('code_challenge') || ''
  const challengeMth = params.get('code_challenge_method') || 'S256'
  const resource     = params.get('resource') || ''   // RFC 8707 (MCP connector)

  const scopes = (scope.replace(/,/g, ' ').split(/\s+/).filter(Boolean))
  const shownScopes = scopes.length ? scopes : ['cases:read', 'conversations:read', 'search', 'ask']

  let redirectHost = ''
  try {
    const u = new URL(redirectUri)
    if (u.protocol === 'https:') redirectHost = u.host
  } catch { /* invalid */ }

  const invalid = !clientId || !redirectHost

  useEffect(() => {
    if (loading) return
    if (!user) {
      navigate(`/login?next=${encodeURIComponent(location.pathname + location.search)}`, { replace: true })
    }
  }, [user, loading, navigate, location.pathname, location.search])

  async function decide(approve: boolean) {
    if (!token || busy) return
    setBusy(true); setError('')
    try {
      const r = await fetch(`${API}/oauth/authorize/approve`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          client_id: clientId, redirect_uri: redirectUri, scope, state,
          code_challenge: challenge, code_challenge_method: challengeMth, resource, approve,
        }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(typeof d.detail === 'string' ? d.detail : 'Could not complete the connection')
      }
      const { redirect_to } = await r.json()
      // Only ever send the browser back to the https redirect URI this page was opened with.
      const dest = new URL(redirect_to)
      if (dest.protocol !== 'https:' || dest.host !== redirectHost) throw new Error('Unexpected redirect')
      window.location.href = dest.toString()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Something went wrong')
      setBusy(false)
    }
  }

  if (loading || !user) return null

  return (
    <>
      <Helmet>
        <title>{`Connect an app — ${APP_NAME}`}</title>
        <meta name="robots" content="noindex" />
      </Helmet>
      <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
        <div className="w-full max-w-md bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="px-6 py-5 border-b border-gray-100 flex items-center gap-3">
            <div className="w-9 h-9 bg-rose-500 rounded-xl flex items-center justify-center">
              <Scale className="w-5 h-5 text-white" />
            </div>
            <p className="text-sm font-semibold text-gray-800">{APP_NAME}</p>
          </div>

          {invalid ? (
            <div className="p-6 text-sm text-red-600">
              This connection request is invalid. Please go back to ChatGPT and try again.
            </div>
          ) : (
            <div className="p-6 space-y-5">
              <div>
                <h1 className="text-lg font-bold text-gray-900">ChatGPT wants read-only access</h1>
                <p className="text-sm text-gray-500 mt-1">
                  Signed in as <span className="font-medium text-gray-700">{user.username}</span>.
                  If you allow this, ChatGPT will be able to:
                </p>
              </div>

              <ul className="space-y-2">
                {shownScopes.map(s => (
                  <li key={s} className="flex items-start gap-2 text-sm text-gray-700">
                    <ShieldCheck className="w-4 h-4 text-rose-500 mt-0.5 shrink-0" />
                    {SCOPE_LABELS[s] || s}
                  </li>
                ))}
              </ul>

              <div className="rounded-xl bg-gray-50 border border-gray-100 px-4 py-3 text-xs text-gray-500 leading-relaxed">
                ChatGPT will <strong>not</strong> be able to change or delete anything, and never sees your password.
                Content it reads is processed by OpenAI under their terms. Access expires automatically and you can
                disconnect at any time from the Connect page.
              </div>

              {error && (
                <div className="rounded-xl bg-red-50 border border-red-100 px-4 py-3 text-sm text-red-600">{error}</div>
              )}

              <div className="flex gap-3">
                <button
                  onClick={() => decide(false)}
                  disabled={busy}
                  className="flex-1 border border-gray-200 text-gray-700 hover:bg-gray-50 rounded-xl px-4 py-2.5 text-sm font-medium disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={() => decide(true)}
                  disabled={busy}
                  className="flex-1 bg-zinc-950 hover:bg-zinc-800 text-white rounded-xl px-4 py-2.5 text-sm font-medium disabled:opacity-50"
                >
                  {busy ? 'Connecting…' : 'Allow'}
                </button>
              </div>
              <p className="text-[11px] text-gray-400 text-center">You will be returned to {redirectHost}</p>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
