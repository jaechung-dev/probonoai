import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Nav from '../components/Nav'
import { Copy, Check, Plus, Trash2, Clock, Plug, X, Menu, Zap } from 'lucide-react'
import { useAuth } from '@/context/auth'
import { API_URL as API, MCP_URL } from '@/lib/config'
import type { MCPToken } from '@/types/mcp'
import ConnectedApps from '../components/ConnectedApps'

function timeAgo(iso: string | null): string {
  if (!iso) return 'Never'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1)  return 'Just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' })
}

function daysLeft(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000)
}

export default function ConnectPage() {
  const { user, token, loading } = useAuth()
  const navigate = useNavigate()

  const [tokens, setTokens]       = useState<MCPToken[]>([])
  const [newName, setNewName]      = useState('')
  const [creating, setCreating]    = useState(false)
  const [newToken, setNewToken]    = useState<string | null>(null)
  const [copied, setCopied]        = useState(false)
  const [copiedCfg, setCopiedCfg] = useState(false)
  const [revoking, setRevoking]    = useState<string | null>(null)
  const [error, setError]          = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const loadTokens = useCallback(async () => {
    if (!token) return
    try {
      const r = await fetch(`${API}/auth/mcp/tokens`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (r.ok) setTokens((await r.json()).tokens)
    } catch {}
  }, [token])

  useEffect(() => {
    if (loading) return
    if (!user) { navigate('/login', { replace: true }); return }
    loadTokens()
  }, [user, loading, navigate, loadTokens])

  async function createToken() {
    if (!token) return
    setCreating(true); setError(''); setNewToken(null)
    try {
      const r = await fetch(`${API}/auth/mcp/token`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body:    JSON.stringify({ name: newName.trim() || 'My MCP Token', expires_days: 30 }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || 'Failed to create token')
      const data = await r.json()
      setNewToken(data.token)
      setNewName('')
      await loadTokens()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create token')
    } finally {
      setCreating(false)
    }
  }

  async function revokeToken(id: string) {
    if (!token) return
    setRevoking(id)
    try {
      await fetch(`${API}/auth/mcp/token/${id}`, {
        method:  'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      setTokens(prev => prev.filter(t => t.id !== id))
    } catch {}
    finally { setRevoking(null) }
  }

  function copy(text: string, which: 'token' | 'cfg') {
    navigator.clipboard.writeText(text)
    if (which === 'token') { setCopied(true);    setTimeout(() => setCopied(false),    2000) }
    else                   { setCopiedCfg(true); setTimeout(() => setCopiedCfg(false), 2000) }
  }

  const cfgSnippet = (t: string) => JSON.stringify({
    mcpServers: {
      'legal-rag': {
        command: 'npx',
        args: ['mcp-remote', MCP_URL, '--header', `Authorization: Bearer ${t}`],
      },
    },
  }, null, 2)

  if (loading || !user) return null

  return (
    <div className="h-screen flex flex-col bg-gray-50">
      <Nav />
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {sidebarOpen && (
          <div className="lg:hidden fixed inset-0 bg-black/40 z-20" onClick={() => setSidebarOpen(false)} />
        )}

        <aside className={`
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
          lg:translate-x-0
          fixed lg:relative z-30 lg:z-auto
          top-0 left-0 h-full lg:h-auto
          w-[240px] shrink-0 flex flex-col
          bg-zinc-950 border-r border-zinc-800
          transition-transform duration-200 ease-in-out
        `}>
          <div className="flex items-center justify-between px-4 pt-5 pb-4 border-b border-zinc-800">
            <div>
              <p className="text-xs font-semibold text-zinc-400 uppercase tracking-widest">Active tokens</p>
              <p className="text-xs text-zinc-500 mt-1">
                {tokens.length === 0 ? 'None yet' : `${tokens.length} token${tokens.length !== 1 ? 's' : ''}`}
              </p>
            </div>
            <button onClick={() => setSidebarOpen(false)} className="lg:hidden text-zinc-500 hover:text-zinc-300 p-1 rounded">
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto py-2">
            {tokens.length === 0 ? (
              <p className="text-xs text-zinc-600 text-center px-4 py-8 leading-relaxed">
                Generate a token to connect Claude Desktop
              </p>
            ) : (
              <ul>
                {tokens.map(t => {
                  const days = daysLeft(t.expires_at)
                  const active = days > 0
                  return (
                    <li key={t.id}>
                      <div className="px-3 py-2.5 flex items-start gap-2.5 group">
                        <span className={`mt-1 w-2 h-2 rounded-full shrink-0 ${active ? 'bg-rose-500' : 'bg-red-500'}`} />
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-medium text-zinc-300 truncate">{t.name}</p>
                          <p className="text-[10px] text-zinc-600 mt-0.5 flex items-center gap-1">
                            <Clock className="w-2.5 h-2.5" />
                            {t.last_used_at ? timeAgo(t.last_used_at) : 'Never used'}
                          </p>
                          <p className={`text-[10px] mt-0.5 ${days < 30 ? 'text-amber-500' : 'text-zinc-600'}`}>
                            {active ? `${days}d left` : 'Expired'}
                          </p>
                        </div>
                        <button
                          onClick={() => revokeToken(t.id)}
                          disabled={revoking === t.id}
                          className="opacity-0 group-hover:opacity-100 p-1 text-zinc-600 hover:text-red-400 transition-all shrink-0"
                          title="Revoke"
                        >
                          {revoking === t.id
                            ? <span className="w-3 h-3 border border-zinc-600 border-t-red-400 rounded-full animate-spin block" />
                            : <Trash2 className="w-3 h-3" />
                          }
                        </button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>

          <div className="px-3 py-3 border-t border-zinc-800">
            <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-500/20">
              <Zap className="w-3.5 h-3.5 text-rose-400 shrink-0" />
              <span className="text-xs text-rose-400 font-medium">MCP ready</span>
            </div>
          </div>
        </aside>

        <main className="flex-1 min-w-0 overflow-y-auto">
          <div className="lg:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-200 sticky top-0 z-10">
            <button onClick={() => setSidebarOpen(true)} className="text-gray-500 hover:text-gray-800 p-1">
              <Menu className="w-5 h-5" />
            </button>
            <span className="text-sm font-semibold text-gray-800">Connect</span>
          </div>

          <div className="max-w-2xl mx-auto px-4 sm:px-6 py-10 space-y-8">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Connect your AI client</h1>
              <p className="text-sm text-gray-500 mt-1.5">
                Use ProBono AI as a legal knowledge tool inside your AI assistant. Generate a token, paste the config,
                and your AI can search NSW legislation and caselaw directly in conversation.
              </p>
            </div>

            <ConnectedApps />

            {/* What this does */}
            <div className="bg-zinc-950 border border-zinc-800 rounded-2xl overflow-hidden">
              <div className="px-5 py-4 border-b border-zinc-800">
                <p className="text-sm font-semibold text-white">How it works</p>
              </div>
              <div className="p-5 space-y-3 text-sm text-zinc-400 leading-relaxed">
                <p>
                  ProBono AI exposes a <span className="text-zinc-200 font-medium">Model Context Protocol (MCP)</span> server
                  that gives your AI assistant live access to NSW legal data — statutes, regulations, and court decisions.
                </p>
                <p>
                  Once connected, you can ask your AI client things like <span className="text-rose-400 italic">"What does the Residential Tenancies Act say about bond refunds?"</span> and
                  it will search the actual legislation and return grounded answers with citations.
                </p>
              </div>
            </div>

            {/* Supported clients */}
            <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-5 py-4 border-b border-gray-100 bg-gray-50">
                <p className="text-sm font-semibold text-gray-700">Supported AI clients</p>
              </div>
              <div className="divide-y divide-gray-100">
                {[
                  { name: 'Claude Desktop', status: 'Supported', detail: 'Native MCP support — paste the config snippet below into claude_desktop_config.json', badge: 'bg-rose-100 text-rose-700' },
                  { name: 'Claude.ai (web)', status: 'Supported', detail: 'Available on Pro and Team plans via the Integrations tab', badge: 'bg-rose-100 text-rose-700' },
                  { name: 'Cursor / Windsurf', status: 'Supported', detail: 'Add the MCP server URL in your editor\'s MCP settings', badge: 'bg-rose-100 text-rose-700' },
                  { name: 'Other MCP clients', status: 'Via mcp-remote', detail: 'Any client that supports the MCP standard works via npx mcp-remote', badge: 'bg-zinc-100 text-zinc-600' },
                ].map(c => (
                  <div key={c.name} className="px-5 py-4 flex items-start justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2.5">
                        <p className="text-sm font-medium text-gray-800">{c.name}</p>
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${c.badge}`}>{c.status}</span>
                      </div>
                      <p className="text-xs text-gray-400 mt-1">{c.detail}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-5 py-4 border-b border-gray-100 bg-gray-50 flex items-center gap-2">
                <Plus className="w-4 h-4 text-gray-500" />
                <p className="text-sm font-semibold text-gray-700">Create new token</p>
              </div>
              <div className="p-5 space-y-3">
                <div className="flex gap-2">
                  <input
                    className="flex-1 border border-gray-200 rounded-xl px-4 py-2.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent shadow-sm placeholder:text-gray-400"
                    placeholder="Token name (e.g. My Claude Desktop)"
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && createToken()}
                  />
                  <button
                    onClick={createToken}
                    disabled={creating}
                    className="bg-zinc-950 hover:bg-zinc-800 text-white rounded-xl px-5 py-2.5 text-sm font-medium transition-all disabled:opacity-50 flex items-center gap-2 shadow-sm shrink-0"
                  >
                    {creating
                      ? <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      : <><Plug className="w-3.5 h-3.5" />Generate</>
                    }
                  </button>
                </div>
                {error && (
                  <div className="rounded-xl bg-red-50 border border-red-100 px-4 py-3 text-sm text-red-600">{error}</div>
                )}
                <p className="text-xs text-gray-400">Token expires in 30 days. You can revoke it at any time.</p>
              </div>
            </div>

            {newToken && (
              <div className="bg-rose-50 border border-rose-200 rounded-2xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-rose-100 flex items-center justify-between">
                  <p className="text-sm font-semibold text-rose-800">Token created — copy it now</p>
                  <span className="text-xs text-rose-600 bg-rose-100 rounded-full px-2.5 py-1">Shown once only</span>
                </div>
                <div className="p-5 space-y-4">
                  <div className="bg-zinc-950 rounded-xl p-4 border border-zinc-800 flex items-start justify-between gap-3">
                    <p className="text-xs font-mono text-rose-400 break-all leading-relaxed">{newToken}</p>
                    <button
                      onClick={() => copy(newToken, 'token')}
                      className="shrink-0 flex items-center gap-1.5 text-xs border border-zinc-700 text-zinc-300 hover:text-white hover:border-zinc-500 rounded-lg px-3 py-1.5 transition-all"
                    >
                      {copied ? <Check className="w-3.5 h-3.5 text-rose-400" /> : <Copy className="w-3.5 h-3.5" />}
                      {copied ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-xs font-semibold text-gray-600">Claude Desktop config</p>
                      <button
                        onClick={() => copy(cfgSnippet(newToken), 'cfg')}
                        className="flex items-center gap-1.5 text-xs border border-gray-200 text-gray-500 hover:text-gray-800 hover:border-gray-300 rounded-lg px-3 py-1.5 transition-all bg-white"
                      >
                        {copiedCfg ? <Check className="w-3.5 h-3.5 text-rose-500" /> : <Copy className="w-3.5 h-3.5" />}
                        {copiedCfg ? 'Copied!' : 'Copy config'}
                      </button>
                    </div>
                    <div className="bg-zinc-950 rounded-xl p-4 border border-zinc-800">
                      <pre className="text-xs text-zinc-300 leading-relaxed overflow-x-auto">{cfgSnippet(newToken)}</pre>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {tokens.length > 0 && (
              <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
                <div className="px-5 py-4 border-b border-gray-100 bg-gray-50">
                  <p className="text-sm font-semibold text-gray-700">
                    Active tokens <span className="text-gray-400 font-normal">({tokens.length})</span>
                  </p>
                </div>
                <div className="divide-y divide-gray-100">
                  {tokens.map(t => (
                    <div key={t.id} className="px-5 py-4 flex items-center justify-between gap-4">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-800 truncate">{t.name}</p>
                        <div className="flex items-center gap-3 mt-1">
                          <span className="flex items-center gap-1 text-xs text-gray-400">
                            <Clock className="w-3 h-3" />
                            Last used: {timeAgo(t.last_used_at)}
                          </span>
                          <span className="text-xs text-gray-300">·</span>
                          <span className="text-xs text-gray-400">Expires {formatDate(t.expires_at)}</span>
                        </div>
                        <div className="flex gap-1 mt-1.5 flex-wrap">
                          {t.scopes.map(s => (
                            <span key={s} className="text-xs bg-gray-100 text-gray-500 rounded-full px-2 py-0.5">{s}</span>
                          ))}
                        </div>
                      </div>
                      <button
                        onClick={() => revokeToken(t.id)}
                        disabled={revoking === t.id}
                        title="Revoke token"
                        className="shrink-0 p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all disabled:opacity-50"
                      >
                        {revoking === t.id
                          ? <span className="w-4 h-4 border-2 border-gray-300 border-t-red-400 rounded-full animate-spin block" />
                          : <Trash2 className="w-4 h-4" />
                        }
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {tokens.length === 0 && !newToken && (
              <div className="text-center py-12 text-gray-400">
                <Plug className="w-8 h-8 mx-auto mb-3 opacity-30" />
                <p className="text-sm">No active tokens. Create one above.</p>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
