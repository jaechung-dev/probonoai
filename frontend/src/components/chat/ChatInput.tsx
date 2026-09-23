import { Send } from 'lucide-react'

type Props = {
  value: string
  onChange: (val: string) => void
  onSend: () => void
  disabled: boolean
}

export default function ChatInput({ value, onChange, onSend, disabled }: Props) {
  return (
    <div className="border-t border-gray-100 px-4 sm:px-6 py-4 bg-white">
      <div className="max-w-2xl mx-auto flex gap-3 items-center">
        <input
          className="flex-1 border border-gray-200 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500 focus:border-transparent transition-all bg-gray-50 focus:bg-white placeholder:text-gray-400 shadow-sm"
          placeholder="Ask a question about NSW law…"
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && onSend()}
          disabled={disabled}
        />
        <button
          onClick={onSend}
          disabled={disabled || !value.trim()}
          aria-label="Send message"
          className="rounded-xl w-11 h-11 bg-gray-900 hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed text-white flex items-center justify-center shrink-0 transition-all shadow-sm"
        >
          <Send className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>
      <p className="max-w-2xl mx-auto mt-2 text-[11px] text-gray-400 text-center leading-snug">
        Not legal advice · For informational purposes only · Questions processed by OpenAI
      </p>
    </div>
  )
}
