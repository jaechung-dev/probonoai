'use client'

import { useState, useCallback } from 'react'
import { useAuth } from '@/context/auth'

const FREE_LIMIT = 2
const STORAGE_KEY = 'iai_anon_count'

function getCount(): number {
  try { return parseInt(localStorage.getItem(STORAGE_KEY) || '0', 10) } catch { return 0 }
}

function increment() {
  try { localStorage.setItem(STORAGE_KEY, String(getCount() + 1)) } catch {}
}

export function useGuestQuota() {
  const { user } = useAuth()
  const [showGate, setShowGate] = useState(false)

  // Call before each question. Returns true if the question can proceed.
  // Client-side check for instant UX; the server enforces the real limit per IP
  // (see services/ai/main.py) so clearing localStorage can't grant more.
  const gate = useCallback((): boolean => {
    if (user) return true
    if (getCount() < FREE_LIMIT) {
      increment()
      return true
    }
    setShowGate(true)
    return false
  }, [user])

  // Open the gate in response to a server-side rejection (HTTP 401 on /chat).
  // Also pin the local counter to the limit so the client stays in sync.
  const forceGate = useCallback(() => {
    try { localStorage.setItem(STORAGE_KEY, String(FREE_LIMIT)) } catch {}
    setShowGate(true)
  }, [])

  const dismissGate = useCallback(() => setShowGate(false), [])

  return { gate, forceGate, showGate, dismissGate, freeLimit: FREE_LIMIT }
}
