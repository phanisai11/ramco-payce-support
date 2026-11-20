import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, RefreshCw } from 'lucide-react'
import { api, type Ticket } from '../api'
import { Badge, StatusBadge, Empty, useTable, Th } from '../components/ui'
import { useTicketDetail } from '../ticket'

const STATUS_FILTERS = ['all', 'open', 'pending_customer', 'in_progress', 'resolved', 'closed'] as const
const CH = 'bg-ink-100 font-semibold uppercase tracking-wider'

export function Tickets({ tenants, names, defaultTenant, presetChannel = '', onPresetConsumed, refreshKey }: {
  tenants: string[]; names: Record<string, string>
  defaultTenant: string; presetChannel?: string
  onPresetConsumed?: () => void; refreshKey: number
}) {
  const { open } = useTicketDetail()
  const [tenant, setTenant] = useState(defaultTenant)
  const [channel, setChannel] = useState('')
  const [status, setStatus] = useState<string>('all')
  const curMonth = new Date().toISOString().slice(0, 7)
  const [month, setMonth] = useState(curMonth)
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [sources, setSources] = useState<string[]>([])
  const [months, setMonths] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [statusBusy, setStatusBusy] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const s = await api.summary(tenant || undefined)
      const srcs = [...new Set(s.tickets.map((t) => t.source).filter(Boolean))] as string[]
      setSources(srcs)
      setMonths([...new Set(s.tickets.map((t) => (t.updated_at ?? '').slice(0, 7)).filter(Boolean))].sort().reverse())
      // Status-card counts reflect tenant+channel scope (before status filter).
      const scoped = s.tickets.filter((t) =>
        (!channel || t.source === channel) &&
        (!month || (t.updated_at ?? '').slice(0, 7) === month))
      const sc: Record<string, number> = {}
      for (const t of scoped) sc[t.status] = (sc[t.status] ?? 0) + 1
      setCounts(sc)
      setTickets(scoped.filter((t) => status === 'all' || t.status === status))
    } catch { setTickets([]) }
    setLoading(false)
  }, [tenant, status, channel, month])

  // Apply tenant preset only when it actually changes (incl. "" = all tenants).
  useEffect(() => { setTenant(defaultTenant) }, [defaultTenant])
  useEffect(() => {
    if (presetChannel) { setChannel(presetChannel); onPresetConsumed?.() }
  }, [presetChannel]) // eslint-disable-line
  useEffect(() => { load() }, [load, refreshKey])

  const setTicketStatus = async (t: Ticket, st: string) => {
    setStatusBusy(t.ticket_id)
    try { await api.ticketStatus(t.tenant_id, t.ticket_id, st); await load() } catch (e: any) { alert(e.message) }
    setStatusBusy('')
  }

  const selCls = 'rounded-lg border border-ink-300 bg-white px-3 py-1.5 text-[13px] text-ink-800 outline-none focus:border-brand-500'

  const cards = useMemo(() => [
    { id: 'all', label: 'All', n: tickets.length, tone: '' },
    ...STATUS_FILTERS.slice(1).map((id) => ({ id, label: id.replaceAll('_', ' '), n: counts[id] ?? 0, tone: '' })),
  ], [tickets, counts])

  const tbl = useTable(tickets, {
    ticket_id: (t) => t.ticket_id,
    tenant: (t) => names[t.tenant_id] ?? t.tenant_id,
    channel: (t) => t.source ?? '',
    priority: (t) => t.priority ?? '',
    status: (t) => t.status,
    queue: (t) => t.queue_id ?? '',
    rule: (t) => t.rule_id ?? '',
    updated: (t) => t.updated_at ?? '',
    csat: (t) => t.csat_score ?? -1,
  })

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">Tickets</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">Every persisted ticket — deterministic routing, saved AI analysis, SLA state.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={tenant} onChange={(e) => setTenant(e.target.value)} className={selCls}>
            <option value="">All tenants</option>
            {tenants.map((t) => <option key={t} value={t}>{names[t] ?? t} ({t})</option>)}
          </select>
          <select value={channel} onChange={(e) => setChannel(e.target.value)} className={selCls}>
            <option value="">All channels</option>
            {sources.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={month} onChange={(e) => setMonth(e.target.value)} className={selCls}>
            <option value="">All months</option>
            {months.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <button onClick={load} className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 bg-white text-ink-500 hover:bg-ink-100">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {cards.map((c) => (
          <button key={c.id} onClick={() => setStatus(c.id)}
            className={`rounded-xl2 border bg-white p-3.5 text-left shadow-card transition-colors ${
              status === c.id ? 'border-brand-300 ring-2 ring-brand-100' : 'border-ink-200 hover:border-ink-300'}`}>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">{c.label}</div>
            <div className="mt-1 text-2xl font-bold tabular-nums text-ink-900">{c.n}</div>
          </button>
        ))}
      </div>

      <div className="overflow-hidden rounded-xl2 border border-ink-200 bg-white shadow-card">
        {loading ? <div className="p-10 text-center text-sm text-ink-400">Loading…</div>
        : tickets.length === 0 ? <Empty text={channel || status !== 'all' || tenant
            ? 'No tickets match the current filters — try clearing the channel/status/tenant filters.'
            : 'No tickets yet — submit one from the Intake tab.'} />
        : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-ink-200 text-left text-ink-500">
                <Th t={tbl} k="ticket_id" className={CH}>Ticket</Th>
                <Th t={tbl} k="tenant" className={CH}>Tenant</Th>
                <Th t={tbl} k="channel" className={CH}>Channel</Th>
                <Th t={tbl} k="priority" className={CH}>Priority</Th>
                <Th t={tbl} k="status" className={CH}>Status</Th>
                <Th t={tbl} k="queue" className={CH}>Queue</Th>
                <Th t={tbl} k="rule" className={CH}>Rule</Th>
                <Th t={tbl} k="updated" className={CH}>Updated</Th>
                <Th t={tbl} k="csat" className={CH}>CSAT</Th>
                <th className={`px-4 py-3 ${CH}`}>Set status</th>
              </tr>
            </thead>
            <tbody>
              {tbl.visible.map((t) => (
                <tr key={`${t.tenant_id}/${t.ticket_id}`}
                  onClick={() => open({ tenant: t.tenant_id, ticket: t.ticket_id })}
                  className="cursor-pointer border-b border-ink-50 hover:bg-ink-50/70">
                  <td className="px-4 py-3 font-mono font-semibold text-brand-600">
                    {t.ticket_id}
                    {t.dedup_of && (
                      <span className="ml-1.5 rounded bg-violet-50 px-1 py-0.5 font-sans text-[10px] font-semibold text-violet-600"
                        title={`Duplicate of ${t.dedup_of}`}>dup → {t.dedup_of}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-ink-700">{names[t.tenant_id] ?? t.tenant_id}</td>
                  <td className="px-4 py-3">{t.source ? <Badge tone="in_progress">{t.source}</Badge> : <span className="text-ink-400">—</span>}</td>
                  <td className="px-4 py-3">{t.priority ? <Badge tone={t.priority.toLowerCase()}>{t.priority.toUpperCase()}</Badge> : <span className="text-ink-400">—</span>}</td>
                  <td className="px-4 py-3"><StatusBadge status={t.status} /></td>
                  <td className="px-4 py-3 text-ink-700">{t.queue_id ?? '—'}</td>
                  <td className="px-4 py-3"><code className="rounded bg-ink-100 px-1.5 py-0.5 text-[11px] text-ink-600">{t.rule_id ?? '—'}</code></td>
                  <td className="px-4 py-3 tabular-nums text-ink-500">{(t.updated_at ?? '').slice(0, 16).replace('T', ' ')}</td>
                  <td className="px-4 py-3">
                    {t.csat_score != null ? (
                      <span className="text-[12px] font-semibold text-amber-500" title={t.csat_comment ?? ''}>
                        {'★'.repeat(t.csat_score)}<span className="text-ink-200">{'★'.repeat(5 - t.csat_score)}</span>
                      </span>
                    ) : <span className="text-ink-300">—</span>}
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <div className="relative inline-block">
                      <select value={t.status} disabled={statusBusy === t.ticket_id}
                        onChange={(e) => setTicketStatus(t, e.target.value)}
                        className="appearance-none rounded-lg border border-ink-200 bg-white py-1 pl-2 pr-7 text-[12px] text-ink-700 outline-none focus:border-brand-500 disabled:opacity-50">
                        {['open', 'pending_customer', 'in_progress', 'resolved', 'closed'].map((s) =>
                          <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}
                      </select>
                      <ChevronDown size={12} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-ink-400" />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

    </div>
  )
}
