'use client'

import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { API_URL as API } from '@/lib/config'
type User = {
  username: string
  name: string
  role: string
  email_verified: boolean
}

type AuthCtx = {
  user: User | null
  token: string | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  register: (name: string, email: string, password: string) => Promise<{ email_verified: boolean }>
  loginWithToken: (token: string) => void
  logout: () => Promise<void>
  refreshAuth: () => Promise<boolean>
  resendVerification: () => Promise<void>
}

const AuthContext = createContext<AuthCtx | null>(null)


function safeStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null
    localStorage.getItem('__probe__')
    return localStorage
  } catch { return null }
}

function isExpired(token: string): boolean {
  try {
    const p = JSON.parse(atob(token.split('.')[1]))
    return p.exp * 1000 < Date.now()
  } catch { return true }
}

function isExpiringSoon(token: string, withinMs = 5 * 60 * 1000): boolean {
  try {
    const p = JSON.parse(atob(token.split('.')[1]))
    return p.exp * 1000 < Date.now() + withinMs
  } catch { return true }
}

function decode(token: string): User {
  const p = JSON.parse(atob(token.split('.')[1]))
  return {
    username:       p.sub,
    name:           p.name,
    role:           p.role,
    email_verified: p.email_verified ?? true,
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Synchronously derive initial auth state from localStorage so the very
  // first render is already authenticated (no FOUC). The static HTML is
  // always pre-rendered unauthenticated, so body needs suppressHydrationWarning.
  const [user, setUser] = useState<User | null>(() => {
    const ls = safeStorage()
    const t = ls?.getItem('iai_token')
    if (t && !isExpired(t)) return decode(t)
    return null
  })
  const [token, setToken] = useState<string | null>(() => {
    const ls = safeStorage()
    const t = ls?.getItem('iai_token')
    if (t && !isExpired(t)) return t
    return null
  })
  // loading = true only when there is a session flag but no valid access token
  // (need a network round-trip to exchange the httpOnly refresh cookie).
  const [loading, setLoading] = useState<boolean>(() => {
    const ls = safeStorage()
    if (!ls) return false
    const t = ls.getItem('iai_token')
    if (t && !isExpired(t)) return false
    return !!ls.getItem('iai_has_session')
  })

  const _store = useCallback((access: string) => {
    const ls = safeStorage()
    ls?.setItem('iai_token', access)
    ls?.setItem('iai_has_session', '1')
    setToken(access)
    setUser(decode(access))
  }, [])

  const refreshAuth = useCallback(async (): Promise<boolean> => {
    const ls = safeStorage()
    if (!ls?.getItem('iai_has_session')) return false
    try {
      const res = await fetch(`${API}/auth/refresh`, {
        method:      'POST',
        credentials: 'include',  // browser sends the httpOnly iai_refresh cookie
      })
      if (!res.ok) throw new Error()
      const data = await res.json()
      _store(data.access_token)
      return true
    } catch {
      ls?.removeItem('iai_token')
      ls?.removeItem('iai_has_session')
      setToken(null)
      setUser(null)
      return false
    }
  }, [_store])

  // On mount: token/user are already set from the synchronous initializers above.
  // This effect only handles two async cases:
  //   1. Valid token expiring soon → pre-emptive background refresh
  //   2. Expired token but session flag set → refresh before marking not-loading
  useEffect(() => {
    const ls = safeStorage()
    if (!ls) { setLoading(false); return }
    const t   = ls.getItem('iai_token')
    const has = !!ls.getItem('iai_has_session')
    if (t && !isExpired(t)) {
      if (has && isExpiringSoon(t)) refreshAuth()
    } else if (has) {
      refreshAuth().finally(() => setLoading(false))
    } else {
      ls.removeItem('iai_token')
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch(`${API}/auth/login`, {
      method:      'POST',
      headers:     { 'Content-Type': 'application/json' },
      credentials: 'include',  // receive httpOnly refresh cookie
      body:        JSON.stringify({ username, password }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Login failed')
    }
    const data = await res.json()
    _store(data.access_token)
    safeStorage()?.removeItem('iai_anon_count')
  }, [_store])

  const register = useCallback(async (name: string, email: string, password: string) => {
    const res = await fetch(`${API}/auth/register`, {
      method:      'POST',
      headers:     { 'Content-Type': 'application/json' },
      credentials: 'include',  // receive httpOnly refresh cookie
      body:        JSON.stringify({ name, email, password }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Registration failed')
    }
    const data = await res.json()
    _store(data.access_token)
    safeStorage()?.removeItem('iai_anon_count')
    return { email_verified: data.user?.email_verified ?? false }
  }, [_store])

  const loginWithToken = useCallback((access: string) => {
    _store(access)
    safeStorage()?.removeItem('iai_anon_count')
  }, [_store])

  const logout = useCallback(async () => {
    const ls = safeStorage()
    try {
      await fetch(`${API}/auth/logout`, {
        method:      'POST',
        credentials: 'include',  // sends httpOnly cookie so server can delete it
      })
    } catch {}
    ls?.removeItem('iai_token')
    ls?.removeItem('iai_has_session')
    ls?.removeItem('iai_anon_count')
    // Wipe any locally-cached intake drafts so private legal matter text never
    // lingers for the next account on this browser.
    try {
      if (ls) {
        for (let i = ls.length - 1; i >= 0; i--) {
          const k = ls.key(i)
          if (k && k.startsWith('iai_intake_draft')) ls.removeItem(k)
        }
      }
    } catch {}
    setToken(null)
    setUser(null)
  }, [])

  const resendVerification = useCallback(async () => {
    if (!user) return
    await fetch(`${API}/auth/resend-verification`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ email: user.username }),
    })
  }, [user])

  return (
    <AuthContext.Provider value={{
      user, token, loading, login, register, loginWithToken,
      logout, refreshAuth, resendVerification,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
