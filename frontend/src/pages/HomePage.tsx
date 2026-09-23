import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { Helmet } from 'react-helmet-async'
import { ArrowRight, Search, MessageSquare, Clock, Plug, LogIn, Scale } from 'lucide-react'
import { useAuth } from '@/context/auth'
import LoginModal from '../components/LoginModal'
import { APP_DOMAIN, APP_NAME, APP_URL } from '@/lib/config'

// Canonical origin for the landing page. The site is reachable at the apex,
// www, and preview subdomains + CloudFront's SPA fallback serves this HTML for
// every route, so we pin an absolute canonical to "/" on the www host to stop
// crawlers indexing deep app routes (e.g. /chat) as the homepage.
const CANONICAL_ORIGIN = (APP_URL && APP_URL.startsWith('https://'))
  ? APP_URL.replace(/\/$/, '')
  : `https://www.${APP_DOMAIN}`
const SEO_TITLE = `${APP_NAME} — Free NSW legal help in plain English`
const SEO_DESC =
  'Ask about NSW law and get plain-English answers backed by real legislation ' +
  'and caselaw. Free legal information for everyday people — a starting point, not legal advice.'

const FEATURES = [
  { icon: Search,        text: 'NSW legislation & caselaw search' },
  { icon: MessageSquare, text: 'Plain English answers with citations' },
  { icon: Clock,         text: 'AI-powered case timeline analysis' },
  { icon: Plug,          text: 'Connect Claude via MCP' },
]

const SAMPLE_QUESTIONS = [
  'What are my rights if my landlord won\'t fix the heating?',
  'Can my employer make me work overtime without extra pay?',
  'How long do I have to file a civil claim in NSW?',
  'What happens at a first court mention?',
]

export default function HomePage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const preview = searchParams.get('preview') !== null
  const [showModal, setShowModal] = useState(false)

  // Logged-in users are bounced to the app — the boot gate in index.html does
  // this before React loads; this is the in-app SPA-navigation fallback.
  // `?preview=1` (nav logo) lets a signed-in user revisit the landing.
  // We do NOT gate the render on a `ready` flag: the landing must render on the
  // first client render so it hydrates cleanly against the pre-rendered HTML
  // (a null first render caused a mismatch → the landing rendered twice).
  useEffect(() => {
    if (window.location.hostname.startsWith('stage.')) {
      navigate('/chat', { replace: true }); return
    }
    if (user && !preview) navigate('/chat', { replace: true })
  }, [user, preview, navigate])

  // CTAs: enter the app if already signed in, else open the login modal.
  const cta = () => (user ? navigate('/chat') : setShowModal(true))

  return (
    <>
      <Helmet>
        <title>{SEO_TITLE}</title>
        <meta name="description" content={SEO_DESC} />
        <link rel="canonical" href={`${CANONICAL_ORIGIN}/`} />
        <meta name="robots" content="index,follow" />

        {/* Open Graph */}
        <meta property="og:type" content="website" />
        <meta property="og:site_name" content={APP_NAME} />
        <meta property="og:title" content={SEO_TITLE} />
        <meta property="og:description" content={SEO_DESC} />
        <meta property="og:url" content={`${CANONICAL_ORIGIN}/`} />
        <meta property="og:image" content={`${CANONICAL_ORIGIN}/justiti.png`} />

        {/* Twitter */}
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:title" content={SEO_TITLE} />
        <meta name="twitter:description" content={SEO_DESC} />
        <meta name="twitter:image" content={`${CANONICAL_ORIGIN}/justiti.png`} />
      </Helmet>

      {showModal && <LoginModal onClose={() => setShowModal(false)} />}

      <div className="min-h-screen flex flex-col bg-zinc-950 text-white">
        {/* Nav */}
        <header className="absolute top-0 left-0 right-0 z-10 flex items-center justify-between px-8 py-5">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-amber-400 to-amber-600 flex items-center justify-center shrink-0 shadow-sm">
              <Scale className="w-5 h-5 text-zinc-950" strokeWidth={2.5} />
            </div>
            <span className="font-serif text-lg font-semibold text-white tracking-wide">{APP_NAME}</span>
          </div>
          <button
            onClick={cta}
            className="flex items-center gap-1.5 text-sm font-medium text-zinc-400 hover:text-white transition-colors"
          >
            {user ? 'Open app' : 'Sign in'} <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </header>

        {/* Hero */}
        <section className="flex-1 flex flex-col lg:flex-row relative" style={{ minHeight: '100svh' }}>
          {/* Deep crimson divine glow */}
          <div className="absolute inset-0 pointer-events-none" style={{
            background: 'radial-gradient(ellipse 55% 65% at 22% 52%, rgba(136,19,55,0.09) 0%, transparent 65%)',
          }} />

          {/* Left — content */}
          <div className="flex flex-col justify-center px-8 sm:px-16 lg:px-24 pt-28 pb-16 lg:pt-24 lg:pb-0 lg:w-[58%] z-10">

            {/* Latin inscription */}
            <p className="text-[10px] tracking-[0.35em] font-semibold text-amber-500/55 uppercase mb-4">
              Iustitia · Veritas · Lex
            </p>

            {/* Upper gold frieze */}
            <div className="flex items-center gap-1 mb-5 w-60">
              <div className="h-px w-2 bg-amber-800/40" />
              <div className="w-px h-2.5 bg-amber-600/50 shrink-0" />
              <div className="h-px flex-1 bg-amber-600/55" />
              <div className="w-1.5 h-1.5 rotate-45 bg-amber-500/75 shrink-0" />
              <div className="h-px w-2 bg-amber-400/90" />
              <div className="w-3 h-3 rotate-45 bg-amber-400 shrink-0" />
              <div className="h-px w-2 bg-amber-400/90" />
              <div className="w-1.5 h-1.5 rotate-45 bg-amber-500/75 shrink-0" />
              <div className="h-px flex-1 bg-amber-600/55" />
              <div className="w-px h-2.5 bg-amber-600/50 shrink-0" />
              <div className="h-px w-2 bg-amber-800/40" />
            </div>

            <h1 className="font-serif text-5xl sm:text-6xl xl:text-[4.5rem] font-bold text-white leading-[1.08] tracking-tight mb-4">
              Legal help<br />
              <span className="text-rose-400">in plain English</span>
            </h1>

            {/* Lower frieze */}
            <div className="flex items-center gap-1 mb-7 w-44">
              <div className="h-px flex-1 bg-amber-700/35" />
              <div className="w-1 h-1 rotate-45 bg-amber-600/55 shrink-0" />
              <div className="h-px w-4 bg-amber-600/45" />
              <div className="w-1 h-1 rotate-45 bg-amber-600/55 shrink-0" />
              <div className="h-px flex-1 bg-amber-700/35" />
            </div>

            <p className="text-sm text-zinc-400 max-w-md mb-7 leading-relaxed">
              Search NSW legislation, ask questions, and get AI-powered analysis grounded in real court decisions — at no cost.
            </p>

            {/* Stone inscription quote */}
            <div className="relative max-w-md mb-8 pl-6">
              <div className="absolute left-0 top-0 bottom-0 flex flex-col items-center">
                <div className="w-1.5 h-1.5 rotate-45 bg-amber-500/70 shrink-0" />
                <div className="w-px flex-1 bg-amber-500/25 mt-1 mb-1" />
                <div className="w-1.5 h-1.5 rotate-45 bg-amber-500/70 shrink-0" />
              </div>
              <p className="text-sm text-zinc-400 leading-relaxed italic">
                "Everyone deserves to understand their legal situation, not just those who can afford a lawyer."
              </p>
            </div>

            <div className="flex flex-col sm:flex-row gap-3 mb-6">
              <button
                onClick={cta}
                className="flex items-center justify-center gap-2 bg-amber-600 hover:bg-amber-500 text-zinc-950 rounded-xl px-8 py-3.5 text-sm font-bold transition-all shadow-lg shadow-amber-900/40"
              >
                {user ? 'Open app' : 'Sign in'} <ArrowRight className="w-4 h-4" />
              </button>
              {!user && (
                <button
                  onClick={cta}
                  className="flex items-center justify-center gap-2 border border-zinc-700 hover:border-amber-600/50 text-zinc-400 hover:text-white rounded-xl px-8 py-3.5 text-sm font-semibold transition-all"
                >
                  Create account
                </button>
              )}
            </div>

            {/* Demo login */}
            {!user && (
              <div className="flex items-center gap-3 bg-zinc-900/80 border border-zinc-800 rounded-xl px-4 py-3 mb-10 max-w-sm">
                <LogIn className="w-4 h-4 text-rose-400 shrink-0" />
                <p className="text-xs text-zinc-400">
                  Try instantly —{' '}
                  <button onClick={cta} className="text-rose-400 hover:text-rose-300 font-semibold transition-colors">
                    sign in
                  </button>
                  {' '}with <span className="font-mono text-zinc-300">demo</span> / <span className="font-mono text-zinc-300">demo1234</span>
                </p>
              </div>
            )}

            {/* Sample questions */}
            <div className="max-w-lg">
              <p className="text-xs text-zinc-400 uppercase tracking-widest font-medium mb-3">Example questions</p>
              <div className="space-y-2">
                {SAMPLE_QUESTIONS.map(q => (
                  <button
                    key={q}
                    onClick={cta}
                    className="w-full text-left flex items-start gap-3 bg-zinc-900/50 hover:bg-zinc-800/70 border border-zinc-800 hover:border-zinc-700 rounded-xl px-4 py-3 transition-all group"
                  >
                    <Search className="w-3.5 h-3.5 text-zinc-400 group-hover:text-rose-400 mt-0.5 shrink-0 transition-colors" />
                    <span className="text-sm text-zinc-400 group-hover:text-zinc-200 transition-colors leading-snug">{q}</span>
                    <ArrowRight className="w-3.5 h-3.5 text-zinc-400 group-hover:text-rose-400 ml-auto shrink-0 mt-0.5 transition-colors" />
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Right — Lady Justice */}
          <div className="hidden lg:block lg:w-[42%] relative overflow-hidden">
            {/* Top vignette */}
            <div className="absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-zinc-950 to-transparent pointer-events-none z-10" />
            {/* Bottom vignette */}
            <div className="absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-zinc-950 to-transparent pointer-events-none z-10" />

            <div
              role="img"
              aria-label="A statue of Lady Justice in flowing robes, holding a set of evenly balanced scales aloft — the classical emblem of law, fairness, and impartial judgment"
              className="absolute inset-0" style={{
              backgroundImage: 'url(/justiti.png)',
              backgroundSize: 'auto 94%',
              backgroundPosition: '38% bottom',
              backgroundRepeat: 'no-repeat',
              filter: 'invert(1) brightness(0.65) sepia(0.15)',
            }} />
            <div className="absolute inset-y-0 left-0 w-40 bg-gradient-to-r from-zinc-950 to-transparent pointer-events-none z-10" />

            {/* Feature chips */}
            <div className="absolute bottom-16 right-8 space-y-2 z-20">
              {FEATURES.map(({ icon: Icon, text }) => (
                <div key={text} className="flex items-center gap-2 bg-zinc-950/85 border border-zinc-800/80 rounded-lg px-3 py-2 backdrop-blur-sm">
                  <div className="w-1 h-1 rotate-45 bg-amber-500/60 shrink-0" />
                  <Icon className="w-3.5 h-3.5 text-rose-400/80 shrink-0" />
                  <span className="text-xs text-zinc-400">{text}</span>
                </div>
              ))}
            </div>

            {/* Decorative column line */}
            <div className="absolute top-24 bottom-24 left-12 w-px z-20" style={{
              background: 'linear-gradient(to bottom, transparent, rgba(217,119,6,0.25) 20%, rgba(217,119,6,0.25) 80%, transparent)',
            }} />
          </div>
        </section>

        {/* Footer */}
        <footer className="border-t border-zinc-800/60 bg-zinc-950">
          <div className="max-w-6xl mx-auto px-8 py-8 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
            <div className="flex items-center gap-3">
              <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-amber-400 to-amber-600 flex items-center justify-center shrink-0">
                <Scale className="w-4 h-4 text-zinc-950" strokeWidth={2.5} />
              </div>
              <div>
                <p className="font-serif font-semibold text-white text-sm">{APP_NAME}</p>
                <p className="text-xs text-zinc-400 mt-0.5">Not legal advice · For informational purposes only · © 2026</p>
              </div>
            </div>
            <div className="flex items-center gap-6 flex-wrap">
              <button onClick={cta} className="text-xs text-zinc-400 hover:text-zinc-300 transition-colors">Search</button>
              <button onClick={cta} className="text-xs text-zinc-400 hover:text-zinc-300 transition-colors">Ask a question</button>
              <button onClick={cta} className="text-xs text-zinc-400 hover:text-zinc-300 transition-colors">Connect AI</button>
              <Link to="/privacy" className="text-xs text-zinc-400 hover:text-zinc-300 transition-colors">Privacy</Link>
              <Link to="/terms" className="text-xs text-zinc-400 hover:text-zinc-300 transition-colors">Terms</Link>
            </div>
          </div>
        </footer>
      </div>
    </>
  )
}
