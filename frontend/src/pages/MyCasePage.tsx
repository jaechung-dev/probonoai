import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Nav from '../components/Nav'
import Spinner from '@/components/Spinner'
import SummarySection from '../components/case/SummarySection'
import TimelineSection from '../components/case/TimelineSection'
import CasesSection from '../components/case/CasesSection'
import DocumentsSection from '../components/case/DocumentsSection'
import ProfileSection from '../components/case/ProfileSection'
import { useAuth } from '@/context/auth'
import { API_URL as API } from '@/lib/config'
import {
  LayoutDashboard, CalendarDays, FolderOpen, FileText, UserCircle, Plus, Menu,
} from 'lucide-react'
import type { FileMeta, CaseSummary, CaseDetail } from '@/types/case'

const MATTER_LABELS: Record<string, string> = {
  criminal: 'Criminal', family: 'Family', civil: 'Civil',
  housing: 'Housing', employment: 'Employment', debt: 'Debt',
  consumer: 'Consumer', other: 'Other',
}

const SECTIONS = [
  { id: 'summary',   label: 'Summary',          Icon: LayoutDashboard },
  { id: 'timeline',  label: 'Timeline',          Icon: CalendarDays    },
  { id: 'cases',     label: 'Manage Cases',      Icon: FolderOpen      },
  { id: 'documents', label: 'Manage Documents',  Icon: FileText        },
  { id: 'profile',   label: 'My Information',    Icon: UserCircle      },
] as const

type SectionId = typeof SECTIONS[number]['id']

export default function MyCasePage() {
  const { user, token, loading: authLoading } = useAuth()
  const navigate = useNavigate()
  const [section, setSection]           = useState<SectionId>('summary')
  const [cases, setCases]               = useState<CaseSummary[]>([])
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null)
  const [detail, setDetail]             = useState<CaseDetail | null>(null)
  const [loadingCases, setLoadingCases] = useState(true)
  const fetchedDetailForRef             = useRef<string | null>(null)
  const [sidebarOpen, setSidebarOpen]   = useState(false)

  useEffect(() => {
    if (!authLoading && !user) navigate('/login', { replace: true })
  }, [authLoading, user, navigate])

  useEffect(() => {
    if (!token) return
    setLoadingCases(true)
    fetch(`${API}/user/cases`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : [])
      .then((data: CaseSummary[]) => {
        setCases(data)
        if (data.length > 0) setActiveCaseId(data[0].id)
        setLoadingCases(false)
      })
      .catch(() => setLoadingCases(false))
  }, [token])

  useEffect(() => {
    if (!activeCaseId || !token) return
    if (fetchedDetailForRef.current === activeCaseId) return
    fetchedDetailForRef.current = activeCaseId
    setDetail(null)
    fetch(`${API}/case/${activeCaseId}`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => setDetail(d))
      .catch(() => {})
  }, [activeCaseId, token])

  const handleDeleted = useCallback((id: string) => {
    setCases(prev => {
      const next = prev.filter(c => c.id !== id)
      if (activeCaseId === id) {
        const nextId = next[0]?.id ?? null
        fetchedDetailForRef.current = null
        setActiveCaseId(nextId)
        setDetail(null)
      }
      return next
    })
  }, [activeCaseId])

  const handleFilesUpdated = useCallback((files: FileMeta[]) => {
    setDetail(prev => prev ? { ...prev, files } : prev)
    setCases(prev => prev.map(c => c.id === activeCaseId ? { ...c, file_count: files.length } : c))
  }, [activeCaseId])

  if (authLoading) return <div className="h-screen flex flex-col bg-gray-50"><Nav /><Spinner /></div>
  if (!user) return null

  const noCases   = !loadingCases && cases.length === 0
  const needsCase = noCases && section !== 'cases' && section !== 'profile'
  const activeCase = cases.find(c => c.id === activeCaseId)

  return (
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
          <div className="px-4 pt-5 pb-4 border-b border-zinc-800">
            <p className="text-xs font-semibold text-zinc-400 uppercase tracking-widest">My Case</p>
            {activeCase && (
              <p className="text-xs text-zinc-500 mt-1.5 truncate">
                {MATTER_LABELS[activeCase.matter?.matterType ?? ''] ?? 'Active case'}
                {activeCase.matter?.subType ? ` · ${activeCase.matter.subType}` : ''}
              </p>
            )}
          </div>

          <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto">
            {SECTIONS.map(({ id, label, Icon }) => {
              const disabled = noCases && id !== 'cases' && id !== 'profile'
              return (
                <button key={id}
                  onClick={() => { if (!disabled) { setSection(id); setSidebarOpen(false) } }}
                  className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm font-medium transition-all text-left ${
                    section === id
                      ? 'bg-rose-500/10 text-rose-400 ring-1 ring-rose-500/20'
                      : disabled ? 'text-zinc-700 cursor-not-allowed'
                      : 'text-zinc-400 hover:text-white hover:bg-white/5'
                  }`}
                >
                  <Icon className="w-4 h-4 shrink-0" />{label}
                </button>
              )
            })}
          </nav>

          <div className="px-3 py-3 border-t border-zinc-800">
            <Link to="/intake?step=2"
              className="flex items-center gap-2 w-full bg-rose-500 hover:bg-rose-600 text-white text-sm font-medium px-3 py-2.5 rounded-lg transition-colors">
              <Plus className="w-4 h-4 shrink-0" /> New case
            </Link>
          </div>
        </aside>

        <main className="flex-1 min-w-0 overflow-y-auto">
          <div className="lg:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-200 sticky top-0 z-10">
            <button onClick={() => setSidebarOpen(true)} className="text-gray-500 hover:text-gray-800 p-1">
              <Menu className="w-5 h-5" />
            </button>
            <span className="text-sm font-semibold text-gray-800">
              {SECTIONS.find(s => s.id === section)?.label}
            </span>
          </div>

          <div className="max-w-2xl mx-auto px-4 sm:px-6 py-8">
            {needsCase ? (
              <div className="text-center py-20">
                <div className="w-14 h-14 bg-gray-100 rounded-2xl flex items-center justify-center mx-auto mb-5">
                  <FolderOpen className="w-7 h-7 text-gray-400" />
                </div>
                <h2 className="text-lg font-bold text-gray-900 mb-2">No case on file</h2>
                <p className="text-sm text-gray-500 mb-6 max-w-xs mx-auto">
                  Submit your case details and documents to unlock the full dashboard.
                </p>
                <Link to="/intake?step=2"
                  className="inline-flex items-center gap-2 bg-gray-900 hover:bg-gray-800 text-white text-sm font-medium px-5 py-2.5 rounded-xl transition-all shadow-sm">
                  <Plus className="w-4 h-4" /> Start intake
                </Link>
              </div>
            ) : (
              <>
                {section === 'summary'   && <SummarySection detail={detail} />}
                {section === 'timeline'  && activeCaseId && <TimelineSection caseId={activeCaseId} />}
                {section === 'cases'     && (
                  <CasesSection
                    cases={cases} activeCaseId={activeCaseId ?? ''} token={token}
                    onActivate={id => { setActiveCaseId(id); setSection('summary') }}
                    onDeleted={handleDeleted}
                  />
                )}
                {section === 'documents' && activeCaseId && (
                  <DocumentsSection
                    detail={detail} caseId={activeCaseId} token={token}
                    onFilesUpdated={handleFilesUpdated}
                  />
                )}
                {section === 'profile' && <ProfileSection />}
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
