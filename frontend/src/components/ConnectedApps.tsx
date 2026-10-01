import { useCallback, useEffect, useState } from 'react'
import { Clock, Trash2, Link2 } from 'lucide-react'
import { useAuth } from '@/context/auth'
import { API_URL as API } from '@/lib/config'

type Grant = {
  id: string
  app: string
  scopes: string[]
  connected_at: string | null
  last_used_at: string | null
}

function ago(iso: string | null): string {
  if (!iso) return 'Never used'
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (m < 1) return 'Just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`
}

/** Apps (e.g. ChatGPT) the user has connected via OAuth, with one-click disconnect. */
export default function ConnectedApps() {
  const { token } = useAuth()
  const [grants, setGrants] = useState<Grant[]>([])
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!token) return
    try {
      const r = await fetch(`${API}/oauth/grants`, { headers: { Authorization: `Bearer ${token}` } })
      if (r.ok) setGrants((await r.json()).grants)
    } catch { /* ignore */ }
  }, [token])

  useEffect(() => { load() }, [load])

  async function disconnect(id: string) {
    if (!token) return
    setBusy(id)
    try {
      const r = await fetch(`${API}/oauth/grants/${id}`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
      })
      if (r.ok) setGrants(g => g.filter(x => x.id !== id))
    } finally { setBusy(null) }
  }

  if (grants.length === 0) return null

  return (
    <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100 bg-gray-50 flex items-center gap-2">
        <Link2 className="w-4 h-4 text-gray-500" />
        <p className="text-sm font-semibold text-gray-700">
          Connected apps <span className="text-gray-400 font-normal">({grants.length})</span>
        </p>
      </div>
      <div className="divide-y divide-gray-100">
        {grants.map(g => (
          <div key={g.id} className="px-5 py-4 flex items-center justify-between gap-4">
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-800">{g.app}</p>
              <p className="text-xs text-gray-400 mt-1 flex items-center gap-1">
                <Clock className="w-3 h-3" /> Last used {ago(g.last_used_at)} · read-only
              </p>
            </div>
            <button
              onClick={() => disconnect(g.id)}
              disabled={busy === g.id}
              className="text-xs border border-gray-200 text-gray-600 hover:text-red-600 hover:border-red-200 rounded-lg px-3 py-1.5 flex items-center gap-1.5 disabled:opacity-50"
            >
              <Trash2 className="w-3 h-3" /> Disconnect
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
