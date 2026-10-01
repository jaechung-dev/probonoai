import { useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Scale } from 'lucide-react'
import { useAuth } from '@/context/auth'
import { takeNext } from '@/lib/nextPath'

export default function AuthCallbackPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { loginWithToken } = useAuth()

  useEffect(() => {
    const token = params.get('token')
    if (!token) {
      navigate('/login?error=invalid_link', { replace: true })
      return
    }
    try {
      loginWithToken(token)
      navigate(takeNext() || '/chat', { replace: true })
    } catch {
      navigate('/login?error=invalid_link', { replace: true })
    }
  }, [])

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <div className="w-10 h-10 bg-zinc-950 rounded-xl flex items-center justify-center">
          <Scale className="w-5 h-5 text-rose-400" />
        </div>
        <div className="flex gap-1.5">
          {[0, 1, 2].map(i => (
            <div key={i} className="w-2 h-2 bg-rose-500 rounded-full animate-bounce"
              style={{ animationDelay: `${i * 0.15}s` }} />
          ))}
        </div>
        <p className="text-sm text-gray-500">Signing you in…</p>
      </div>
    </div>
  )
}
