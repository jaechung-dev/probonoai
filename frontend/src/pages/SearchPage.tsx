import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { Search, MessageSquare, ChevronDown, Clock, X, Menu } from 'lucide-react'
import Nav from '../components/Nav'
import LoginModal from '../components/LoginModal'
import SearchResult from '../components/search/SearchResult'
import { useGuestQuota } from '@/hooks/useGuestQuota'
import { useAuth } from '@/context/auth'
import { API_URL as API } from '@/lib/config'
import type { SearchResult as SearchResultType, Mode, SearchSource, RecentSearch } from '@/types/search'

const SOURCE_LABELS: Record<SearchSource, string> = {
  legislation: 'Legislation', caselaw: 'Caselaw', both: 'Both', case_events: 'Case Events',
}
const SOURCE_COLORS: Record<SearchSource, string> = {
  legislation: 'bg-violet-100 text-violet-700', caselaw: 'bg-sky-100 text-sky-700',
  both: 'bg-gray-100 text-gray-600', case_events: 'bg-rose-100 text-rose-700',
}

const STORAGE_KEY = 'search_recents'
const MAX_RECENTS = 20

function loadRecents(): RecentSearch[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]') } catch { return [] }
}
function saveRecent(entry: RecentSearch) {
  const prev = loadRecents().filter(r => r.query !== entry.query || r.mode !== entry.mode)
  localStorage.setItem(STORAGE_KEY, JSON.stringify([entry, ...prev].slice(0, MAX_RECENTS)))
}
function timeAgo(ts: number): string {
  const m = Math.floor((Date.now() - ts) / 60000)
  if (m < 1) return 'Just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export default function SearchPage() {
  const [mode, setMode]         = useState<Mode>('search')
  const [query, setQuery]       = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const [source, setSource]     = useState<SearchSource>('legislation')
  const [results, setResults]   = useState<SearchResultType[]>([])
  const [answer, setAnswer]     = useState('')
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const [recents, setRecents]   = useState<RecentSearch[]>([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)
  const { gate, forceGate, showGate, dismissGate } = useGuestQuota()
  const { token } = useAuth()
  // Signed-in users must send their token, or the server counts them against
  // the anonymous per-IP quota.
  const headers = (): Record<string, string> => ({
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  })

  useEffect(() => { setRecents(loadRecents()) }, [])

  const pushRecent = useCallback((q: string, m: Mode, s: SearchSource) => {
    saveRecent({ query: q, mode: m, source: s, ts: Date.now() })
    setRecents(loadRecents())
  }, [])

  async function doSearch(q = query, s = source) {
    if (!q.trim() || !gate()) return
    setLoading(true); setResults([]); setError('')
    try {
      const r = await fetch(`${API}/search`, {
        method: 'POST', headers: headers(),
        body: JSON.stringify({ query: q, source: s, k: 8 }),
      })
      if (r.status === 429) throw new Error('Too many searches. Please wait a while or log in.')
      if (!r.ok) throw new Error(`Server error ${r.status}`)
      const d = await r.json()
      setResults(d.results); pushRecent(q, 'search', s)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reach the API')
    } finally { setLoading(false) }
  }

  async function doAsk(q = query, s = source) {
    if (!q.trim() || !gate()) return
    setLoading(true); setAnswer(''); setError('')
    let quotaBlocked = false
    try {
      await fetchEventSource(`${API}/ask`, {
        method: 'POST', headers: headers(), openWhenHidden: true,
        body: JSON.stringify({ question: q, source: s, k: 6 }),
        async onopen(res) {
          // Server-side free-question limit for anonymous users.
          if (res.status === 401 && !token) { quotaBlocked = true; throw new Error('quota') }
        },
        onmessage(ev) {
          if (ev.data === '[DONE]') return
          try { const chunk = JSON.parse(ev.data); if (chunk.text) setAnswer(prev => prev + chunk.text) } catch {}
        },
        onclose() {}, onerror(err) { throw err },
      })
      pushRecent(q, 'ask', s)
    } catch (e) {
      if (quotaBlocked) forceGate()
      else setError(e instanceof Error ? e.message : 'Could not reach the API')
    } finally { setLoading(false) }
  }

  function runRecent(r: RecentSearch) {
    setQuery(r.query); setSource(r.source); setMode(r.mode)
    setResults([]); setAnswer(''); setError(''); setSidebarOpen(false)
    if (r.mode === 'search') doSearch(r.query, r.source)
    else doAsk(r.query, r.source)
  }

  function handleAsk(title: string) {
    setMode('ask'); setQuery(`Explain ${title} in plain English`)
    setExpandedIdx(null); setResults([]); setAnswer('')
  }

  return (
    <>
      {showGate && <LoginModal onClose={dismissGate} />}
      <div className="h-screen flex flex-col bg-gray-50">
        <Nav />
        <div className="flex flex-1 min-h-0 overflow-hidden">
          {sidebarOpen && (
            <div className="lg:hidden fixed inset-0 bg-black/40 z-20" onClick={() => setSidebarOpen(false)} />
          )}

          <aside className={`
            ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
            lg:translate-x-0 fixed lg:relative z-30 lg:z-auto
            top-0 left-0 h-full lg:h-auto
            w-[240px] shrink-0 flex flex-col
            bg-zinc-950 border-r border-zinc-800
            transition-transform duration-200 ease-in-out
          `}>
            <div className="flex items-center justify-between px-4 pt-5 pb-4 border-b border-zinc-800">
              <p className="text-xs font-semibold text-zinc-400 uppercase tracking-widest">Recent searches</p>
              <button onClick={() => setSidebarOpen(false)} className="lg:hidden text-zinc-500 hover:text-zinc-300 p-1 rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto py-2">
              {recents.length === 0 ? (
                <p className="text-xs text-zinc-600 text-center px-4 py-8 leading-relaxed">Your searches will appear here</p>
              ) : (
                <ul>
                  {recents.map((r, i) => (
                    <li key={i}>
                      <button onClick={() => runRecent(r)}
                        className="w-full text-left px-3 py-2.5 group flex flex-col gap-1 text-zinc-400 hover:bg-zinc-900 hover:text-white transition-colors">
                        <div className="flex items-start gap-1.5 min-w-0">
                          {r.mode === 'search'
                            ? <Search className="w-3 h-3 shrink-0 mt-0.5 opacity-50" />
                            : <MessageSquare className="w-3 h-3 shrink-0 mt-0.5 opacity-50" />
                          }
                          <span className="text-xs truncate leading-relaxed">{r.query}</span>
                        </div>
                        <div className="flex items-center gap-1.5 pl-4">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${SOURCE_COLORS[r.source]}`}>
                            {SOURCE_LABELS[r.source]}
                          </span>
                          <span className="text-[10px] text-zinc-600 flex items-center gap-1">
                            <Clock className="w-2.5 h-2.5" />{timeAgo(r.ts)}
                          </span>
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {recents.length > 0 && (
              <div className="px-3 py-3 border-t border-zinc-800">
                <button onClick={() => { localStorage.removeItem(STORAGE_KEY); setRecents([]) }}
                  className="w-full text-xs text-zinc-600 hover:text-zinc-400 py-2 transition-colors">
                  Clear history
                </button>
              </div>
            )}
          </aside>

          <main className="flex-1 min-w-0 overflow-y-auto">
            <div className="lg:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-200 sticky top-0 z-10">
              <button onClick={() => setSidebarOpen(true)} className="text-gray-500 hover:text-gray-800 p-1">
                <Menu className="w-5 h-5" />
              </button>
              <span className="text-sm font-semibold text-gray-800">Search &amp; Ask</span>
            </div>

            <div className="max-w-3xl mx-auto px-4 sm:px-6 py-10 space-y-8">
              <div>
                <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Search &amp; Ask</h1>
                <p className="text-sm text-gray-500 mt-1">Search NSW legislation and caselaw, or ask a legal question in plain English</p>
              </div>

              <div className="space-y-3">
                <div className="flex gap-1 bg-gray-100 rounded-xl p-1 w-fit">
                  {([
                    { m: 'search' as Mode, Icon: Search,        label: 'Search' },
                    { m: 'ask'    as Mode, Icon: MessageSquare, label: 'Ask'    },
                  ]).map(({ m, Icon, label }) => (
                    <button key={m}
                      onClick={() => { setMode(m); setResults([]); setAnswer(''); setError('') }}
                      className={`flex items-center gap-1.5 px-5 py-2 text-sm font-medium rounded-lg transition-all ${
                        mode === m ? 'bg-white text-gray-900 shadow-sm border border-gray-200' : 'text-gray-500 hover:text-gray-700'
                      }`}
                    >
                      <Icon className="w-3.5 h-3.5" />{label}
                    </button>
                  ))}
                </div>

                <div className="flex gap-2 flex-col sm:flex-row">
                  <div className="relative flex-1">
                    {mode === 'search'
                      ? <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                      : <MessageSquare className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                    }
                    <input
                      ref={inputRef}
                      className="w-full border border-gray-200 rounded-xl pl-10 pr-10 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent transition-all bg-white shadow-sm placeholder:text-gray-400"
                      placeholder={mode === 'search' ? 'Search NSW legislation and caselaw…' : 'Ask a legal question…'}
                      value={query}
                      onChange={e => setQuery(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && (mode === 'search' ? doSearch() : doAsk())}
                    />
                    {query && (
                      <button
                        type="button"
                        aria-label="Clear"
                        onClick={() => { setQuery(''); inputRef.current?.focus() }}
                        className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-all"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <div className="relative">
                      <select
                        className="appearance-none border border-gray-200 rounded-xl pl-3 pr-8 py-3 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent text-gray-700 shadow-sm cursor-pointer"
                        value={source}
                        onChange={e => setSource(e.target.value as SearchSource)}
                      >
                        <option value="legislation">Legislation</option>
                        <option value="caselaw">Caselaw</option>
                        <option value="both">Both</option>
                        <option value="case_events">Case Events</option>
                      </select>
                      <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
                    </div>
                    <button
                      onClick={() => mode === 'search' ? doSearch() : doAsk()}
                      disabled={loading}
                      className="bg-gray-900 hover:bg-gray-800 text-white rounded-xl px-6 text-sm font-medium transition-all disabled:opacity-50 shadow-sm"
                    >
                      {loading ? (
                        <span className="flex items-center gap-2">
                          <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                          {mode === 'search' ? 'Searching' : 'Asking'}
                        </span>
                      ) : (mode === 'search' ? 'Search' : 'Ask')}
                    </button>
                  </div>
                </div>
              </div>

              {error && (
                <div className="rounded-xl bg-red-50 border border-red-100 px-4 py-3 text-sm text-red-600">{error}</div>
              )}

              {mode === 'search' && results.length > 0 && (
                <div className="space-y-3">
                  <p className="text-xs text-gray-400 font-medium">
                    {results.length} result{results.length !== 1 ? 's' : ''} for &ldquo;{query}&rdquo;
                  </p>
                  {results.map((r, i) => (
                    <SearchResult
                      key={i}
                      result={r}
                      expanded={expandedIdx === i}
                      onToggle={() => setExpandedIdx(expandedIdx === i ? null : i)}
                      onAsk={handleAsk}
                    />
                  ))}
                </div>
              )}

              {mode === 'ask' && (answer || loading) && (
                <div className="bg-white border border-gray-100 rounded-xl overflow-hidden shadow-sm">
                  <div className="flex items-center gap-2 px-5 py-3.5 border-b border-gray-100 bg-gray-50">
                    <div className="w-6 h-6 bg-gray-900 rounded-lg flex items-center justify-center">
                      <MessageSquare className="w-3.5 h-3.5 text-rose-400" />
                    </div>
                    <p className="text-xs font-semibold text-gray-500 uppercase tracking-widest">Answer</p>
                    {loading && (
                      <span className="ml-auto flex items-center gap-1.5 text-xs text-gray-400">
                        <span className="w-3 h-3 border-2 border-gray-300 border-t-rose-500 rounded-full animate-spin" />
                        Generating
                      </span>
                    )}
                  </div>
                  <div className="px-5 py-5">
                    <div className="max-h-[60vh] overflow-y-auto">
                      <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
                        {answer}{loading && <span className="animate-pulse text-rose-400">▌</span>}
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </main>
        </div>
      </div>
    </>
  )
}
