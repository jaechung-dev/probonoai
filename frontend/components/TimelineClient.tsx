'use client'

import { useState, useEffect, useId } from 'react'
import { Paperclip, X } from 'lucide-react'
export type { CaseEvent } from '@/types/case'
import type { CaseEvent } from '@/types/case'

const CATEGORIES = ['All', 'Court', 'Police', 'Medical', 'Correspondence', 'Personal', 'Offence', 'Investigation', 'Submissions', 'Verdict', 'Other']

const COLORS: Record<string, { badge: string; dot: string; ring: string }> = {
  Offence:        { badge: 'bg-rose-50 text-rose-700 border-rose-100',        dot: 'bg-rose-400',    ring: 'ring-rose-100' },
  Investigation:  { badge: 'bg-amber-50 text-amber-700 border-amber-100',     dot: 'bg-amber-400',   ring: 'ring-amber-100' },
  Police:         { badge: 'bg-sky-50 text-sky-700 border-sky-100',           dot: 'bg-sky-400',     ring: 'ring-sky-100' },
  Court:          { badge: 'bg-violet-50 text-violet-700 border-violet-100',  dot: 'bg-violet-400',  ring: 'ring-violet-100' },
  Submissions:    { badge: 'bg-yellow-50 text-yellow-700 border-yellow-100',  dot: 'bg-yellow-400',  ring: 'ring-yellow-100' },
  Verdict:        { badge: 'bg-rose-50 text-rose-700 border-rose-100', dot: 'bg-rose-400', ring: 'ring-rose-100' },
  Medical:        { badge: 'bg-teal-50 text-teal-700 border-teal-100',        dot: 'bg-teal-400',    ring: 'ring-teal-100' },
  Correspondence: { badge: 'bg-indigo-50 text-indigo-700 border-indigo-100',  dot: 'bg-indigo-400',  ring: 'ring-indigo-100' },
  Personal:       { badge: 'bg-pink-50 text-pink-700 border-pink-100',        dot: 'bg-pink-400',    ring: 'ring-pink-100' },
  Other:          { badge: 'bg-gray-50 text-gray-600 border-gray-100',        dot: 'bg-gray-300',    ring: 'ring-gray-100' },
}
const DEFAULT = { badge: 'bg-gray-50 text-gray-600 border-gray-100', dot: 'bg-gray-300', ring: 'ring-gray-100' }

export default function TimelineClient({ events }: { events: CaseEvent[] }) {
  const [selected, setSelected] = useState<CaseEvent | null>(null)
  const [filter, setFilter] = useState('All')

  const filtered = filter === 'All' ? events : events.filter(e => e.category === filter)
  const presentCats = CATEGORIES.filter(cat =>
    cat === 'All' || events.some(e => e.category === cat)
  )

  return (
    <>
      {/* Category filter chips — only show categories that have events */}
      <div className="flex gap-2 flex-wrap mb-6">
        {presentCats.map(cat => {
          const count = cat === 'All' ? events.length : events.filter(e => e.category === cat).length
          const c = cat === 'All' ? null : (COLORS[cat] || DEFAULT)
          return (
            <button
              key={cat}
              onClick={() => setFilter(cat)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-all border ${
                filter === cat
                  ? cat === 'All'
                    ? 'bg-gray-900 text-white border-gray-900 shadow-sm'
                    : `${c!.badge} border-current shadow-sm`
                  : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300 hover:text-gray-700'
              }`}
            >
              {cat}
              {cat !== 'All' && (
                <span className="ml-1.5 opacity-60 font-normal">{count}</span>
              )}
            </button>
          )
        })}
      </div>

      {/* Vertical timeline */}
      <div className="overflow-y-auto" style={{ maxHeight: 'calc(100vh - 260px)' }}>
        {filtered.map((e, i) => {
          const c = COLORS[e.category] || DEFAULT
          const isFirst = i === 0
          const isLast = i === filtered.length - 1

          return (
            <div key={i} className="flex gap-5">
              {/* Track: line + dot + line */}
              <div className="flex flex-col items-center w-4 shrink-0">
                <div className={`w-px flex-none h-5 ${isFirst ? 'bg-transparent' : 'bg-gray-200'}`} />
                <div className={`w-2.5 h-2.5 rounded-full shrink-0 ring-2 ring-white ${c.dot}`} />
                <div className={`w-px flex-1 min-h-[36px] ${isLast ? 'bg-transparent' : 'bg-gray-200'}`} />
              </div>

              {/* Content card */}
              <div
                data-testid="timeline-row"
                onClick={() => setSelected(e)}
                className="flex-1 pb-6 cursor-pointer group"
              >
                <div
                  className="bg-white border border-gray-100 rounded-xl px-4 py-3.5 shadow-sm hover:shadow-md hover:border-gray-200 transition-all group-hover:-translate-y-0.5"
                >
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs text-gray-400 tabular-nums font-mono">{e.date}</span>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${c.badge}`}>
                      {e.category}
                    </span>
                  </div>
                  <p className="text-sm font-semibold text-gray-800 leading-snug mb-1">
                    {e.subject}
                  </p>
                  {e.summary && (
                    <p className="text-xs text-gray-400 line-clamp-2 leading-relaxed">
                      {e.summary}
                    </p>
                  )}
                  {e.attachments?.length > 0 && (
                    <div className="mt-2 flex items-center gap-1 text-xs text-gray-400">
                      <Paperclip className="w-3 h-3" />
                      {e.attachments.length} attachment{e.attachments.length > 1 ? 's' : ''}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Detail modal */}
      {selected && <EventModal event={selected} onClose={() => setSelected(null)} />}
    </>
  )
}

function EventModal({ event: e, onClose }: { event: CaseEvent; onClose: () => void }) {
  const c = COLORS[e.category] || DEFAULT
  const titleId = useId()
  useEffect(() => {
    const handler = (ev: KeyboardEvent) => { if (ev.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50" onClick={onClose}>
      <div className="relative w-full max-w-2xl max-h-[85vh] flex flex-col bg-white rounded-2xl shadow-2xl overflow-hidden"
        role="dialog" aria-modal="true" aria-labelledby={titleId}
        onClick={ev => ev.stopPropagation()}>
        <div className="px-6 pt-6 pb-4 border-b border-gray-100 shrink-0">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border ${c.badge}`}>
                {e.category}
              </span>
              <span className="text-xs text-gray-400 font-mono tabular-nums">{e.date}</span>
            </div>
            <button onClick={onClose} aria-label="Close" className="shrink-0 p-1 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-all">
              <X className="w-4 h-4" />
            </button>
          </div>
          <h2 id={titleId} className="text-base font-semibold text-gray-900 leading-snug">{e.subject}</h2>
          {e.summary && <p className="text-sm text-gray-500 mt-1 leading-relaxed">{e.summary}</p>}
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          <div className="bg-gray-50 rounded-xl p-5 border border-gray-100">
            <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{e.content}</p>
          </div>
          {e.attachments && e.attachments.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Attachments</p>
              <div className="flex gap-2 flex-wrap">
                {e.attachments.map((a, idx) => (
                  <span key={idx} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs text-gray-600 font-medium shadow-sm">
                    <Paperclip className="w-3 h-3 text-gray-400" />
                    {a.name}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
