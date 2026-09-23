import { useState, useRef, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Helmet } from 'react-helmet-async'
import { Scale, ArrowRight, Check, Eye, EyeOff } from 'lucide-react'
import { useAuth } from '@/context/auth'
import { API_URL as API, APP_DOMAIN, APP_NAME } from '@/lib/config'

const FEATURES = [
  'NSW legislation & caselaw search',
  'Plain English answers with citations',
  'AI-powered case timeline analysis',
  'Connect Claude via MCP',
]

export default function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [name, setName]         = useState('')
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm]   = useState('')
  const [showPw, setShowPw]     = useState(false)
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [verifyEmail, setVerifyEmail] = useState('')
  const [agreedToTerms, setAgreedToTerms] = useState(false)

  const [otp, setOtp]             = useState<string[]>(Array(6).fill(''))
  const [otpError, setOtpError]   = useState('')
  const [otpLoading, setOtpLoading] = useState(false)
  const [cooldown, setCooldown]   = useState(0)
  const otpRefs = useRef<(HTMLInputElement | null)[]>([])

  useEffect(() => {
    if (cooldown <= 0) return
    const t = setTimeout(() => setCooldown(c => c - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (password !== confirm) { setError('Passwords do not match'); return }
    if (password.length < 8)  { setError('Password must be at least 8 characters'); return }
    if (!agreedToTerms)       { setError('Please agree to the Terms of Service and Privacy Policy'); return }
    setLoading(true)
    try {
      await register(name, email, password)
      setVerifyEmail(email)
      setCooldown(60)
      setTimeout(() => otpRefs.current[0]?.focus(), 100)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setLoading(false)
    }
  }

  function handleOtpChange(i: number, val: string) {
    const digit = val.replace(/\D/g, '').slice(-1)
    const next = [...otp]; next[i] = digit; setOtp(next)
    if (digit && i < 5) otpRefs.current[i + 1]?.focus()
  }

  function handleOtpKeyDown(i: number, e: React.KeyboardEvent) {
    if (e.key === 'Backspace' && !otp[i] && i > 0) otpRefs.current[i - 1]?.focus()
  }

  function handleOtpPaste(e: React.ClipboardEvent) {
    e.preventDefault()
    const digits = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6)
    setOtp([...digits.split(''), ...Array(6 - digits.length).fill('')])
    otpRefs.current[Math.min(digits.length, 5)]?.focus()
  }

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault()
    setOtpError('')
    const code = otp.join('')
    if (code.length !== 6) { setOtpError('Please enter all 6 digits'); return }
    setOtpLoading(true)
    try {
      const res = await fetch(`${API}/auth/verify-otp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: verifyEmail, code }),
      })
      if (!res.ok) {
        const d = await res.json()
        setOtpError(d.detail || 'Invalid code. Please try again.')
      } else {
        navigate('/login?verified=1')
      }
    } catch {
      setOtpError('Network error. Please try again.')
    } finally {
      setOtpLoading(false)
    }
  }

  async function handleResend() {
    setCooldown(60); setOtp(Array(6).fill('')); setOtpError('')
    await fetch(`${API}/auth/resend-verification`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: verifyEmail }),
    })
    setTimeout(() => otpRefs.current[0]?.focus(), 100)
  }

  const passwordStrength = password.length === 0 ? null
    : password.length < 8  ? 'weak'
    : password.length < 12 ? 'fair'
    : /[A-Z]/.test(password) && /[0-9]/.test(password) ? 'strong' : 'good'

  const strengthColor = { weak: 'bg-rose-400', fair: 'bg-amber-400', good: 'bg-emerald-400', strong: 'bg-emerald-500' }
  const strengthWidth = { weak: 'w-1/4', fair: 'w-2/4', good: 'w-3/4', strong: 'w-full' }

  if (verifyEmail) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-6">
      <div className="w-full max-w-sm text-center space-y-6">
        <div className="w-16 h-16 bg-rose-100 rounded-2xl flex items-center justify-center mx-auto">
          <svg className="w-8 h-8 text-rose-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Enter verification code</h1>
          <p className="text-sm text-gray-500 mt-2">We emailed a 6-digit code to <span className="font-medium text-gray-700">{verifyEmail}</span></p>
        </div>
        <form onSubmit={handleVerify} className="space-y-5">
          <div className="flex gap-2 justify-center" onPaste={handleOtpPaste}>
            {otp.map((digit, i) => (
              <input
                key={i} ref={el => { otpRefs.current[i] = el }}
                type="text" inputMode="numeric" maxLength={1} value={digit}
                onChange={e => handleOtpChange(i, e.target.value)}
                onKeyDown={e => handleOtpKeyDown(i, e)}
                className="w-12 h-14 text-center text-xl font-bold border border-gray-200 bg-white rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent transition-all shadow-sm"
                aria-label={`Digit ${i + 1}`}
              />
            ))}
          </div>
          {otpError && <div className="rounded-xl bg-red-50 border border-red-100 px-4 py-3 text-sm text-red-600 text-left">{otpError}</div>}
          <button type="submit" disabled={otpLoading || otp.join('').length !== 6}
            className="w-full bg-zinc-950 hover:bg-zinc-800 text-white rounded-xl h-11 text-sm font-medium transition-all disabled:opacity-50 flex items-center justify-center gap-2 shadow-lg">
            {otpLoading ? 'Verifying…' : <>Verify email <ArrowRight className="w-4 h-4" /></>}
          </button>
        </form>
        <p className="text-xs text-gray-400">
          Didn't receive it?{' '}
          {cooldown > 0 ? <span className="text-gray-400">Resend in {cooldown}s</span>
            : <button onClick={handleResend} className="text-rose-600 hover:underline">Resend code</button>}
        </p>
      </div>
    </div>
  )

  return (
    <>
      <Helmet>
        <title>Create account — {APP_NAME}</title>
        <meta name="robots" content="noindex" />
      </Helmet>
    <div className="min-h-screen flex bg-white">
      <div className="hidden lg:flex lg:w-[45%] bg-zinc-950 flex-col justify-between p-12 relative overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,_rgba(52,211,153,0.08)_0%,_transparent_60%)]" />
        <Link to="/" className="relative flex items-center gap-2.5 w-fit">
          <div className="w-9 h-9 bg-rose-500 rounded-xl flex items-center justify-center shadow-lg shadow-rose-500/30">
            <Scale className="w-5 h-5 text-white" />
          </div>
          <span className="font-bold text-white text-base tracking-tight">{APP_NAME}</span>
        </Link>
        <div className="relative space-y-10">
          <div>
            <p className="text-xs font-semibold text-rose-400 uppercase tracking-widest mb-3">Free · NSW · AI-powered</p>
            <h2 className="text-4xl font-bold text-white leading-tight">Free access to<br />NSW legal help</h2>
            <p className="text-zinc-400 mt-4 leading-relaxed text-sm max-w-sm">Create your free account to access plain English legal answers, case timelines, and AI-powered legal research.</p>
          </div>
          <div className="space-y-3">
            {FEATURES.map(f => (
              <div key={f} className="flex items-center gap-3 text-sm text-zinc-400">
                <div className="w-5 h-5 rounded-full bg-rose-500/10 border border-rose-500/20 flex items-center justify-center shrink-0">
                  <Check className="w-3 h-3 text-rose-400" />
                </div>
                {f}
              </div>
            ))}
          </div>
        </div>
        <p className="relative text-xs text-zinc-600">{`${APP_DOMAIN} · Not legal advice`}</p>
      </div>

      <div className="flex-1 flex items-center justify-center bg-gray-50 p-6 overflow-y-auto">
        <div className="w-full max-w-sm space-y-6 py-8">
          <Link to="/" className="lg:hidden flex items-center gap-2.5 w-fit">
            <div className="w-8 h-8 bg-zinc-950 rounded-lg flex items-center justify-center">
              <Scale className="w-4 h-4 text-rose-400" />
            </div>
            <span className="font-bold text-gray-900">{APP_NAME}</span>
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Create an account</h1>
            <p className="text-sm text-gray-500 mt-1">Free access to NSW legal research</p>
          </div>
          <button type="button" onClick={() => { window.location.href = `${API}/auth/google` }}
            className="w-full flex items-center justify-center gap-3 bg-white border border-gray-200 rounded-xl h-11 text-sm font-medium text-gray-700 hover:bg-gray-50 hover:border-gray-300 transition-all shadow-sm">
            <GoogleIcon />Continue with Google
          </button>
          <div className="relative flex items-center gap-3">
            <div className="flex-1 border-t border-gray-200" />
            <span className="text-xs text-gray-400 font-medium">or with email</span>
            <div className="flex-1 border-t border-gray-200" />
          </div>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-gray-700">Full name</label>
              <input className="w-full border border-gray-200 bg-white rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent transition-all placeholder:text-gray-400 shadow-sm"
                placeholder="Jane Smith" value={name} onChange={e => setName(e.target.value)} autoComplete="name" required />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-gray-700">Email address</label>
              <input type="email" className="w-full border border-gray-200 bg-white rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent transition-all placeholder:text-gray-400 shadow-sm"
                placeholder="you@example.com" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" required />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-gray-700">Password</label>
              <div className="relative">
                <input type={showPw ? 'text' : 'password'}
                  className="w-full border border-gray-200 bg-white rounded-xl px-4 py-3 pr-11 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent transition-all placeholder:text-gray-400 shadow-sm"
                  placeholder="Min. 8 characters" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" required />
                <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                  {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              {passwordStrength && (
                <div className="space-y-1">
                  <div className="h-1 bg-gray-100 rounded-full overflow-hidden">
                    <div className={`h-1 rounded-full transition-all ${strengthColor[passwordStrength]} ${strengthWidth[passwordStrength]}`} />
                  </div>
                  <p className={`text-xs capitalize ${passwordStrength === 'weak' ? 'text-rose-500' : passwordStrength === 'fair' ? 'text-amber-500' : 'text-emerald-500'}`}>{passwordStrength} password</p>
                </div>
              )}
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-gray-700">Confirm password</label>
              <input type="password"
                className={`w-full border bg-white rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent transition-all placeholder:text-gray-400 shadow-sm ${confirm && confirm !== password ? 'border-rose-300' : 'border-gray-200'}`}
                placeholder="Repeat your password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" required />
            </div>
            <label className="flex items-start gap-3 cursor-pointer group">
              <input
                type="checkbox"
                checked={agreedToTerms}
                onChange={e => setAgreedToTerms(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-gray-300 text-rose-600 focus:ring-rose-500 shrink-0 cursor-pointer"
              />
              <span className="text-xs text-gray-500 leading-relaxed">
                I agree to the{' '}
                <Link to="/terms" target="_blank" className="text-rose-600 hover:text-rose-700 hover:underline font-medium">Terms of Service</Link>
                {' '}and{' '}
                <Link to="/privacy" target="_blank" className="text-rose-600 hover:text-rose-700 hover:underline font-medium">Privacy Policy</Link>
              </span>
            </label>
            {error && <div className="rounded-xl bg-red-50 border border-red-100 px-4 py-3 text-sm text-red-600">{error}</div>}
            <button type="submit" disabled={loading || !agreedToTerms}
              className="w-full bg-zinc-950 hover:bg-zinc-800 text-white rounded-xl h-11 text-sm font-medium transition-all disabled:opacity-50 flex items-center justify-center gap-2 shadow-lg">
              {loading ? 'Creating account…' : <>Create account <ArrowRight className="w-4 h-4" /></>}
            </button>
          </form>
          <p className="text-center text-sm text-gray-500">
            Already have an account?{' '}
            <Link to="/login" className="text-rose-600 hover:text-rose-700 font-medium hover:underline">Sign in</Link>
          </p>
        </div>
      </div>
    </div>
    </>
  )
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
      <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
      <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
      <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
    </svg>
  )
}
