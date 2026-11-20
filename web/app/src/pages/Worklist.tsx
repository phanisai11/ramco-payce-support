import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Inbox } from 'lucide-react'
import { api, type Ticket } from '../api'
import { Badge, StatusBadge, Card, Empty } from '../components/ui'
import { useTicketDetail } from '../ticket'

const NEEDS_HUMAN = new Set(['open', 'in_progress', 'pending_customer', 'pending_agent'])

export function Worklist({ tenants, names, refreshKey }: {
  tenants: string[]; names: Record<string, string>; refreshKey: number
}) {
  const { open } = useTicketDetail()
  const [tenant, setTenant] = useState('')
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const s = await api.summary(tenant || undefined)
      setTickets(s.tickets.filter((t) => NEEDS_HUMAN.has(t.status)))
    } catch { setTickets([]) }
    setLoading(false)
  }, [tenant])

  useEffect(() => { load() }, [load, refreshKey])

  const byQueue = tickets.reduce<Record<string, Ticket[]>>((acc, t) => {
    const q = t.queue_id ?? 'unassigned'
    ;(acc[q] ??= []).push(t)
    return acc
  }, {})

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">Worklist</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">Tickets waiting on a human — pending agent or in progress, grouped by queue.</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={tenant} onChange={(e) => setTenant(e.target.value)}
            className="rounded-lg border border-ink-300 bg-white px-3 py-1.5 text-[13px] text-ink-800 outline-none focus:border-brand-500">
            <option value="">All tenants</option>
            {tenants.map((t) => <option key={t} value={t}>{names[t] ?? t} ({t})</option>)}
          </select>
          <button onClick={load} className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 bg-white text-ink-500 hover:bg-ink-100">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {loading && <p className="py-10 text-center text-sm text-ink-400">Loading…</p>}
      {!loading && tickets.length === 0 && (
        <Card><Empty text="Nothing waiting on a human — the bot handled everything." /></Card>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
        {Object.entries(byQueue).map(([queue, rows]) => (
          <Card key={queue} title={<span className="flex items-center gap-2"><Inbox size={14} className="text-brand-500" />{queue} <Badge tone="draft">{rows.length}</Badge></span>}>
            <div className="flex flex-col gap-2">
              {rows.map((t) => (
                <button key={`${t.tenant_id}/${t.ticket_id}`}
                  onClick={() => open({ tenant: t.tenant_id, ticket: t.ticket_id })}
                  className="rounded-lg border border-ink-100 px-3 py-2.5 text-left transition-colors hover:border-brand-200 hover:bg-brand-25/50">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[13px] font-semibold text-brand-600">{t.ticket_id}</span>
                    <StatusBadge status={t.status} />
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[12px] text-ink-500">
                    <span>{names[t.tenant_id] ?? t.tenant_id}</span>
                    {t.source && <Badge tone="in_progress">{t.source}</Badge>}
                    {t.priority && <Badge tone={t.priority.toLowerCase()}>{t.priority}</Badge>}
                    {t.rule_id && <code className="rounded bg-ink-100 px-1 text-[10px] text-ink-500">{t.rule_id}</code>}
                  </div>
                </button>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
