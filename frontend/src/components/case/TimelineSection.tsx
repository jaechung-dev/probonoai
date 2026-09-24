import { useState, useEffect } from 'react'
import { AlertCircle } from 'lucide-react'
import TimelineClient from '@/components/TimelineClient'
import Spinner from '@/components/Spinner'
import { API_URL as API } from '@/lib/config'
import type { CaseEvent } from '@/components/TimelineClient'

export default function TimelineSection({ caseId }: { caseId: string }) {
  const [events, setEvents] = useState<CaseEvent[] | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    setEvents(null); setErr('')
    fetch(`${API}/case/${caseId}/timeline`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.events) setEvents(d.events)
        else setErr('No timeline events found for this case.')
      })
      .catch(() => setErr('Could not load timeline.'))
  }, [caseId])

  return (
    <div>
      <h2 className="text-xl font-bold text-gray-900 mb-6">Case Timeline</h2>
      {err ? (
        <div className="py-16 text-center">
          <AlertCircle className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-400">{err}</p>
        </div>
      ) : events ? <TimelineClient events={events} /> : <Spinner />}
    </div>
  )
}
