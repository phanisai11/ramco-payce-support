import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, MousePointerClick } from 'lucide-react'
import { api, type Analytics } from '../api'
import { Badge, Card, Empty, useTable, Th } from '../components/ui'

const CHANNEL_META: Record<string, { label: string; icon: string; desc: string }> = {
  widget: { label: 'Web widget', icon: '◈', desc: 'In-app support widget' },
  email: { label: 'Email', icon: '✉', desc: 'Customer email intake' },
  partner: { label: 'Partner portal', icon: '⇄', desc: 'Partner / reseller channel' },
  whatsapp: { label: 'WhatsApp', icon: '◍', desc: 'WhatsApp Business intake' },
  phone: { label: 'Phone', icon: '☎', desc: 'Telephony / call intake' },
}

export function Channels({ tenants, names, onFilter }: {
  tenants: string[]; names: Record<string, string>
  onFilter: (tenant: string, channel: string) => void
}) {
  const [tenant, setTenant] = useState('')
  const [data, setData] = useState<Analytics | null>(null)
  const [lastSync, setLastSync] = useState('')
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await api.analytics(tenant || undefined))
      setLastSync(new Date().toLocaleTimeString())
    } catch { /* keep last good data */ }
  }, [tenant])

  useEffect(() => { load() }, [load])
  // realtime-ish: poll every 15s while the tab is mounted
  useEffect(() => {
    timer.current = setInterval(() => load(), 15000)
    return () => { if (timer.current) clearInterval(timer.current) }
  }, [load])

  const stats = [...(data?.channel_stats ?? [])].sort((a, b) => b.total - a.total)
  const tbl = useTable(stats, {
    channel: (c) => CHANNEL_META[c.channel]?.label ?? c.channel,
    total: (c) => c.total,
    assigned: (c) => c.assigned,
    open: (c) => c.open,
    resolved: (c) => c.resolved,
    bot: (c) => c.bot_solved,
    breached: (c) => c.sla_breached,
  })

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">Channels</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">
            Ticket volume, automation and SLA health per intake source.
            {lastSync && <span className="ml-2 text-[11px] text-ink-400">live · synced {lastSync}</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select value={tenant} onChange={(e) => setTenant(e.target.value)}
            className="rounded-lg border border-ink-300 bg-white px-3 py-1.5 text-[13px] text-ink-800 outline-none focus:border-brand-500">
            <option value="">All tenants</option>
            {tenants.map((t) => <option key={t} value={t}>{names[t] ?? t} ({t})</option>)}
          </select>
          <button onClick={() => load()}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 bg-white text-ink-500 hover:bg-ink-100">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {stats.length === 0 && <Card><Empty text="No channel data yet — submit tickets via different sources." /></Card>}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {stats.map((c) => {
          const src = c.channel
          const meta = CHANNEL_META[src] ?? { label: src, icon: '•', desc: 'Custom channel' }
          const botPct = c.total ? Math.round((c.bot_solved / c.total) * 100) : 0
          return (
            <div key={src}
              className="cursor-pointer rounded-xl2 border border-ink-200 bg-white p-5 shadow-card transition-all hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-md"
              onClick={() => onFilter(tenant, src)}>
              <div className="flex items-start justify-between">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-lg text-brand-500">{meta.icon}</div>
                <Badge tone="in_progress">{meta.label}</Badge>
              </div>
              <div className="mt-3 text-3xl font-bold tabular-nums text-ink-900">{c.total}</div>
              <div className="text-[12px] text-ink-500">{meta.desc}</div>
              <div className="mt-3 grid grid-cols-4 gap-2 border-t border-ink-100 pt-3 text-[12px]">
                <div><div className="font-bold tabular-nums text-ink-800">{c.assigned}</div><div className="text-ink-400">assigned</div></div>
                <div><div className="font-bold tabular-nums text-ink-800">{c.open}</div><div className="text-ink-400">open</div></div>
                <div><div className="font-bold tabular-nums text-ink-800">{c.resolved}</div><div className="text-ink-400">resolved</div></div>
                <div><div className="font-bold tabular-nums text-ink-800">{botPct}%</div><div className="text-ink-400">bot-handled</div></div>
              </div>
              {c.sla_breached > 0 && <div className="mt-2 text-[11px] font-semibold text-error-600">{c.sla_breached} SLA breach{c.sla_breached > 1 ? 'es' : ''}</div>}
            </div>
          )
        })}
      </div>

      <Card title="Channel detail">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-ink-200 text-left text-ink-500">
              <Th t={tbl} k="channel" className="px-4 py-3">Channel</Th>
              <Th t={tbl} k="total" className="px-4 py-3">Total</Th>
              <Th t={tbl} k="assigned" className="px-4 py-3">Assigned</Th>
              <Th t={tbl} k="open" className="px-4 py-3">Open</Th>
              <Th t={tbl} k="resolved" className="px-4 py-3">Resolved</Th>
              <Th t={tbl} k="bot" className="px-4 py-3">Bot-solved</Th>
              <Th t={tbl} k="breached" className="px-4 py-3">SLA breached</Th>
            </tr>
          </thead>
          <tbody>
            {tbl.visible.map((c) => (
              <tr key={c.channel} className="cursor-pointer border-b border-ink-50 hover:bg-ink-50/70"
                onClick={() => onFilter(tenant, c.channel)}>
                <td className="px-4 py-3 font-semibold text-ink-800"><span className="mr-2">{CHANNEL_META[c.channel]?.icon ?? '•'}</span>{CHANNEL_META[c.channel]?.label ?? c.channel}</td>
                <td className="px-4 py-3 tabular-nums">{c.total}</td>
                <td className="px-4 py-3 tabular-nums font-semibold">{c.assigned}</td>
                <td className="px-4 py-3 tabular-nums">{c.open}</td>
                <td className="px-4 py-3 tabular-nums">{c.resolved}</td>
                <td className="px-4 py-3 tabular-nums">{c.bot_solved}</td>
                <td className="px-4 py-3">{c.sla_breached ? <Badge tone="expired">{c.sla_breached}</Badge> : <span className="text-ink-400">0</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="flex items-center gap-1.5 px-4 pb-3 pt-2 text-[11px] text-ink-400">
          <MousePointerClick size={11} /> Click a row to open filtered tickets · auto-refreshes every 15s
        </p>
      </Card>
    </div>
  )
}
