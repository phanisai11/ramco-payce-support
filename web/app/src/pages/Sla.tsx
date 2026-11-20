import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Timer, AlertTriangle, IndianRupee, CalendarRange } from 'lucide-react'
import { api, type SlaQuarters, type SlaReport, type SlaQuarterRow } from '../api'
import { Badge, StatCard, Card, Empty, useTable, Th } from '../components/ui'

export function Sla({ tenants, names }: { tenants: string[]; names: Record<string, string> }) {
  const [tenant, setTenant] = useState('')
  const [report, setReport] = useState<SlaReport | null>(null)
  const [breachedOnly, setBreachedOnly] = useState(false)
  const [qyear, setQyear] = useState<number | ''>('')
  const [quarters, setQuarters] = useState<SlaQuarters | null>(null)
  const curMonth = new Date().toISOString().slice(0, 7)

  const load = useCallback(() => {
    api.sla(tenant || undefined).then(setReport).catch(() => setReport(null))
  }, [tenant])
  useEffect(() => { setReport(null); load() }, [load])

  const loadQuarters = useCallback(() => {
    api.slaQuarters(qyear === '' ? undefined : qyear, tenant || undefined)
      .then((q) => { setQuarters(q); if (qyear === '') setQyear(q.year) })
      .catch(() => setQuarters(null))
  }, [qyear, tenant])
  useEffect(() => { loadQuarters() }, [loadQuarters])

  const evals = report?.evaluations ?? []
  const monthFiltered = evals.filter((e) => e.month === curMonth)
  const shown = breachedOnly ? monthFiltered.filter((e) => e.breach) : monthFiltered
  const sorted = [...shown].sort((a, b) => (b.breach ? 1 : 0) - (a.breach ? 1 : 0) || b.age_hours - a.age_hours)
  const tbl = useTable(sorted, {
    ticket_id: (e) => e.ticket_id,
    tenant: (e) => names[e.tenant_id] ?? e.tenant_id,
    status: (e) => e.status,
    age: (e) => e.age_hours,
    threshold: (e) => e.breach?.unanswered_hours ?? -1,
    rule: (e) => e.breach?.sla_rule_id ?? '',
    penalty: (e) => e.breach ? `${e.breach.currency} ${e.breach.penalty_amount}` : (e.unanswered ? 'unanswered' : ''),
    month: (e) => e.month ?? '',
  })

  const qrows = quarters?.by_tenant ?? []
  const qtbl = useTable(qrows, {
    tenant: (r: SlaQuarterRow) => r.tenant_id ? (names[r.tenant_id] ?? r.tenant_id) : 'All tenants',
    quarter: (r) => r.quarter,
    year: (r) => r.year,
    tickets: (r) => r.tickets,
    open: (r) => r.open,
    unanswered: (r) => r.unanswered,
    breached: (r) => r.breached,
    penalty: (r) => Object.entries(r.penalties).map(([c, a]) => `${c} ${a}`).join(', '),
  })

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">SLA monitoring</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">
            Unanswered-time policy per tenant/status — tickets from <b>{curMonth}</b>, policy version <code className="rounded bg-ink-100 px-1 text-[11px]">{report?.sla_policy_version ?? '—'}</code>.
          </p>
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

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard icon={<Timer size={18} />} label="tickets evaluated" value={report?.total_tickets ?? '—'} />
        <StatCard icon={<AlertTriangle size={18} />} label="unanswered" value={report?.unanswered_count ?? '—'} tone="warn" />
        <StatCard icon={<AlertTriangle size={18} />} label="breached" value={report?.breached_count ?? '—'} tone={(report?.breached_count ?? 0) > 0 ? 'bad' : 'ok'} />
        <StatCard icon={<Timer size={18} />} label="SLA attainment"
          value={report && report.total_tickets ? `${(((report.total_tickets - report.breached_count) / report.total_tickets) * 100).toFixed(1)}%` : '—'}
          tone={report && report.breached_count === 0 ? 'ok' : 'warn'} />
        {Object.entries(report?.penalties_by_currency ?? {}).map(([c, a]) => (
          <StatCard key={c} icon={<IndianRupee size={18} />} label={`penalty (${c})`} value={a} tone="bad" />
        ))}
      </div>

      <Card title="Quarterly SLA exposure"
        action={
          <div className="flex items-center gap-2">
            <CalendarRange size={13} className="text-ink-400" />
            <select value={qyear} onChange={(e) => setQyear(e.target.value ? Number(e.target.value) : '')}
              className="rounded-lg border border-ink-300 bg-white px-2.5 py-1 text-[12px] text-ink-800 outline-none focus:border-brand-500">
              {(quarters?.years ?? []).map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
        }>
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          {(quarters?.quarters ?? []).map((q) => {
            const pen = Object.entries(q.penalties).map(([c, a]) => `${c} ${a}`).join(' · ') || '—'
            return (
              <div key={q.quarter} className="rounded-xl border border-ink-200 bg-ink-50/60 px-3.5 py-3">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">
                  {q.quarter} {q.year}
                </div>
                <div className="mt-1 text-lg font-bold tabular-nums text-ink-900">{q.tickets} <span className="text-[11px] font-medium text-ink-400">tickets</span></div>
                <div className="mt-0.5 text-[11px] text-ink-500">
                  {q.open} open · {q.unanswered} unanswered · <span className={q.breached ? 'font-semibold text-error-600' : ''}>{q.breached} breached</span>
                </div>
                <div className="mt-0.5 text-[11px] font-medium text-error-600">{pen}</div>
              </div>
            )
          })}
        </div>
        {qtbl.visible.length === 0 ? (
          <Empty text={`No tickets in ${qyear || 'selected year'}.`} />
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-ink-200 text-left text-ink-500">
                <Th t={qtbl} k="tenant" className="px-2 py-2.5">Tenant</Th>
                <Th t={qtbl} k="quarter" className="px-2 py-2.5">Quarter</Th>
                <Th t={qtbl} k="tickets" className="px-2 py-2.5">Tickets</Th>
                <Th t={qtbl} k="open" className="px-2 py-2.5">Open</Th>
                <Th t={qtbl} k="unanswered" className="px-2 py-2.5">Unanswered</Th>
                <Th t={qtbl} k="breached" className="px-2 py-2.5">Breached</Th>
                <Th t={qtbl} k="penalty" className="px-2 py-2.5">Penalty exposure</Th>
              </tr>
            </thead>
            <tbody>
              {qtbl.visible.map((r, i) => (
                <tr key={`${r.tenant_id}-${r.quarter}-${i}`} className="border-b border-ink-50 last:border-0">
                  <td className="px-2 py-2.5 font-medium text-ink-800">
                    {r.tenant_id ? (names[r.tenant_id] ?? r.tenant_id) : 'All tenants'}
                  </td>
                  <td className="px-2 py-2.5"><Badge tone="plain">{r.quarter}</Badge></td>
                  <td className="px-2 py-2.5 tabular-nums">{r.tickets}</td>
                  <td className="px-2 py-2.5 tabular-nums">{r.open}</td>
                  <td className="px-2 py-2.5 tabular-nums">{r.unanswered}</td>
                  <td className="px-2 py-2.5 tabular-nums">
                    <span className={r.breached ? 'font-semibold text-error-600' : 'text-ink-400'}>{r.breached}</span>
                  </td>
                  <td className="px-2 py-2.5">
                    {Object.keys(r.penalties).length
                      ? Object.entries(r.penalties).map(([c, a]) => (
                          <Badge key={c} tone="expired">{c} {a}</Badge>
                        ))
                      : <span className="text-ink-400">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Evaluations"
        action={
          <label className="flex cursor-pointer items-center gap-2 text-[12px] font-medium text-ink-600">
            <input type="checkbox" checked={breachedOnly} onChange={(e) => setBreachedOnly(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-ink-300 accent-[#465fff]" />
            breaches only
          </label>
        }>
        {sorted.length === 0 ? <Empty text="No evaluations — submit tickets to see SLA state." /> : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-ink-200 text-left text-ink-500">
                <Th t={tbl} k="ticket_id" className="px-2 py-3">Ticket</Th>
                <Th t={tbl} k="tenant" className="px-2 py-3">Tenant</Th>
                <Th t={tbl} k="status" className="px-2 py-3">Status</Th>
                <Th t={tbl} k="age" className="px-2 py-3">Age (h)</Th>
                <Th t={tbl} k="threshold" className="px-2 py-3">Threshold (h)</Th>
                <Th t={tbl} k="rule" className="px-2 py-3">SLA rule</Th>
                <Th t={tbl} k="penalty" className="px-2 py-3">Penalty</Th>
                <Th t={tbl} k="month" className="px-2 py-3">Month</Th>
              </tr>
            </thead>
            <tbody>
              {tbl.visible.map((e) => (
                <tr key={`${e.tenant_id}/${e.ticket_id}`}
                  className="border-b border-ink-50">
                  <td className="px-2 py-3 font-mono font-semibold text-brand-600">{e.ticket_id}</td>
                  <td className="px-2 py-3 text-ink-700">{names[e.tenant_id] ?? e.tenant_id}</td>
                  <td className="px-2 py-3"><Badge tone={e.status}>{e.status.replaceAll('_', ' ')}</Badge></td>
                  <td className="px-2 py-3 tabular-nums">{e.age_hours}</td>
                  <td className="px-2 py-3 tabular-nums text-ink-500">{e.breach?.unanswered_hours ?? '—'}</td>
                  <td className="px-2 py-3"><code className="rounded bg-ink-100 px-1.5 py-0.5 text-[11px] text-ink-600">{e.breach?.sla_rule_id ?? '—'}</code></td>
                  <td className="px-2 py-3">
                    {e.breach
                      ? <Badge tone="expired">{e.breach.currency} {e.breach.penalty_amount}</Badge>
                      : e.unanswered ? <Badge tone="in_progress">unanswered</Badge>
                      : <span className="text-ink-400">—</span>}
                  </td>
                  <td className="px-2 py-3 text-ink-500">{e.month ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
