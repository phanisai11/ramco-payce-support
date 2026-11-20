import { useEffect, useState } from 'react'
import { Search, ExternalLink } from 'lucide-react'
import { api } from '../api'

export function Header() {
  const [health, setHealth] = useState<'ok' | 'bad' | 'unknown'>('unknown')
  const [clock, setClock] = useState('')

  useEffect(() => {
    const poll = async () => {
      try {
        const h = await api.health()
        setHealth(h.status === 'ok' ? 'ok' : 'bad')
      } catch { setHealth('bad') }
      setClock(new Date().toLocaleTimeString())
    }
    poll()
    const id = setInterval(poll, 10000)
    return () => clearInterval(id)
  }, [])

  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-ink-200 bg-white px-6">
      <div className="flex items-center gap-3">
        <h1 className="text-[15px] font-bold tracking-tight text-ink-900">Payce Support Intelligence</h1>
      </div>
      <div className="flex items-center gap-4">
        <div className="hidden items-center gap-2 rounded-lg border border-transparent bg-ink-100 px-3 py-1.5 text-ink-400 transition-colors focus-within:border-brand-200 focus-within:bg-white md:flex">
          <Search size={14} />
          <input
            placeholder="Search tickets, rules…"
            className="w-44 bg-transparent text-[13px] text-ink-800 outline-none placeholder:text-ink-400"
          />
          <kbd className="rounded border border-ink-200 bg-white px-1 text-[10px] text-ink-400">⌘K</kbd>
        </div>
        <span className="hidden items-center gap-1.5 text-xs text-ink-500 sm:inline-flex">
          <span className={`h-2 w-2 rounded-full ${health === 'ok' ? 'bg-success-500' : health === 'bad' ? 'bg-error-500' : 'bg-warning-500'}`} />
          {health === 'ok' ? 'API healthy' : health === 'bad' ? 'API unreachable' : 'checking…'}
        </span>
        <a href="/dev/ui/" target="_blank" rel="noreferrer"
          className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-500 transition-colors hover:bg-ink-100"
          title="API playground">
          <ExternalLink size={16} />
        </a>
        <div className="flex items-center gap-2.5 border-l border-ink-200 pl-4">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-500 text-[10px] font-bold text-white">AD</span>
          <div className="hidden leading-tight sm:block">
            <div className="text-xs font-semibold text-ink-800">Admin</div>
            <div className="text-[10px] tabular-nums text-ink-400">{clock}</div>
          </div>
        </div>
      </div>
    </header>
  )
}
