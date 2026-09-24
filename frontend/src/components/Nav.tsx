import { useState, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, Search, Plug, LogOut, Menu, X, ClipboardList, Scale } from 'lucide-react'
import { useAuth } from '@/context/auth'
import { APP_NAME, API_URL as API } from '@/lib/config'

const LINKS = [
  { to: '/chat',     label: 'Chat',    Icon: MessageSquare },
  { to: '/search',   label: 'Search',  Icon: Search        },
  { to: '/my-case',  label: 'My Case', Icon: ClipboardList },
  { to: '/connect',  label: 'Connect', Icon: Plug          },
] as const

const MATTER_LABELS: Record<string, string> = {
  criminal: 'Criminal', family: 'Family', civil: 'Civil',
  housing: 'Housing', employment: 'Employment', debt: 'Debt',
  consumer: 'Consumer', other: 'Other',
}

export default function Nav() {
  const { user, token, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [activeCase, setActiveCase] = useState<string | null>(null)

  // Show the logged-in user's real active matter in the nav (not a hardcoded
  // placeholder). Hidden entirely when the user has no case.
  useEffect(() => {
    if (!token) { setActiveCase(null); return }
    let cancelled = false
    fetch(`${API}/user/cases`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : [])
      .then((data) => {
        if (cancelled) return
        const c = Array.isArray(data) ? data[0] : null
        const mt = c?.matter?.matterType
        if (!mt) { setActiveCase(null); return }
        const label = MATTER_LABELS[mt] ?? mt
        setActiveCase(c.matter?.subType ? `${label} · ${c.matter.subType}` : label)
      })
      .catch(() => { if (!cancelled) setActiveCase(null) })
    return () => { cancelled = true }
  }, [token])

  function isActive(to: string) {
    return location.pathname === to || location.pathname.startsWith(to + '/')
  }

  return (
    <header className="bg-zinc-950 border-b border-zinc-800 sticky top-0 z-40">
      <div className="w-full px-4 sm:px-6 h-14 flex items-center gap-6">
        <Link to="/?preview=1" className="flex items-center gap-2.5 shrink-0">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-amber-400 to-amber-600 flex items-center justify-center shrink-0 shadow-sm">
            <Scale className="w-4 h-4 text-zinc-950" strokeWidth={2.5} />
          </div>
          <span className="font-serif font-semibold text-white text-sm tracking-wide hidden sm:block">{APP_NAME}</span>
        </Link>

        <nav className="hidden md:flex items-center justify-center gap-0.5 flex-1">
          {LINKS.map(({ to, label, Icon }) => (
            <Link key={to} to={to}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all ${
                isActive(to)
                  ? 'bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/20'
                  : 'text-zinc-400 hover:text-white hover:bg-white/5'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </Link>
          ))}
        </nav>

        <div className="hidden md:flex items-center gap-4 shrink-0 ml-auto">
          {activeCase && (
            <span className="text-xs text-zinc-400 hidden lg:block px-2.5 py-1 rounded-full bg-zinc-900 border border-zinc-800">
              {activeCase}
            </span>
          )}
          {user ? (
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-zinc-300">{user.name}</span>
              <button
                onClick={async () => { await logout(); navigate('/login') }}
                title="Sign out"
                aria-label="Sign out"
                className="text-zinc-500 hover:text-white transition-colors p-1.5 rounded-md hover:bg-white/5"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <button
              onClick={() => navigate('/login')}
              className="bg-rose-600 hover:bg-rose-500 h-8 px-3 text-xs text-white rounded-md font-medium transition-all shadow-lg shadow-rose-500/20"
            >
              Sign in
            </button>
          )}
        </div>

        <div className="flex md:hidden items-center gap-1 ml-auto">
          {LINKS.map(({ to, Icon }) => (
            <Link key={to} to={to}
              className={`p-2 rounded-md transition-all ${
                isActive(to)
                  ? 'bg-rose-500/10 text-rose-300'
                  : 'text-zinc-500 hover:text-white hover:bg-white/5'
              }`}
            >
              <Icon className="w-4 h-4" />
            </Link>
          ))}
          <button
            onClick={() => setMobileOpen(!mobileOpen)}
            className="p-2 text-zinc-500 hover:text-white hover:bg-white/5 rounded-md transition-all"
          >
            {mobileOpen ? <X className="w-4 h-4" /> : <Menu className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {mobileOpen && (
        <div className="md:hidden border-t border-zinc-800 bg-zinc-950 px-4 py-3 space-y-1">
          <div className="flex items-center justify-between py-2 border-b border-zinc-800 mb-2">
            <span className="text-xs text-zinc-500 font-mono">{activeCase ?? ''}</span>
            {user ? (
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-zinc-400">{user.name}</span>
                <button
                  onClick={async () => { await logout(); navigate('/login'); setMobileOpen(false) }}
                  className="text-zinc-500 hover:text-white p-1"
                >
                  <LogOut className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <button
                onClick={() => { navigate('/login'); setMobileOpen(false) }}
                className="bg-rose-600 hover:bg-rose-500 h-7 px-3 text-xs text-white rounded-md font-medium"
              >
                Sign in
              </button>
            )}
          </div>
          {LINKS.map(({ to, label, Icon }) => (
            <Link key={to} to={to} onClick={() => setMobileOpen(false)}
              className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm font-medium transition-all ${
                isActive(to)
                  ? 'bg-rose-500/10 text-rose-300'
                  : 'text-zinc-400 hover:text-white hover:bg-white/5'
              }`}
            >
              <Icon className="w-4 h-4" />
              {label}
            </Link>
          ))}
        </div>
      )}
    </header>
  )
}
