import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, AlertTriangle, Bot, Users, Timer, IndianRupee, Activity, Radio } from 'lucide-react'
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, type Analytics, type Costs, type SlaReport, type Summary } from '../api'
import { Badge, Card, Empty, useTable, Th } from '../components/ui'

const CHART_COLORS = ['#465fff', '#12b76a', '#f79009', '#f04438', '#7a5af8', '#0ba5ec', '#6172f3', '#98a2b3']

const fmt = (iso?: string | null) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')
const pct = (n: number) => `${Math.round(n * 100)}%`

function Kpi({ icon: Icon, label, value, sub, tone }: {
  icon: any; label: string; value: string; sub?: string; tone?: string
}) {
  return (
    <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-ink-400">
        <Icon size={14} className={tone ?? 'text-brand-500'} /> {label}
      </div>
      <div className="mt-1.5 text-2xl font-bold tabular-nums text-ink-900">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-ink-500">{sub}</div>}
    </div>
  )
}

export function Ops({ names }: { tenants: string[]; names: Record<string, string> }) {
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [sla, setSla] = useState<SlaReport | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [incidents, setIncidents] = useState<any[]>([])
  const [costs, setCosts] = useState<Costs | null>(null)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    Promise.all([api.analytics(), api.sla(), api.summary(), api.incidents()])
      .then(([a, s, m, i]) => {
        setAnalytics(a); setSla(s); setSummary(m); setIncidents(i.incidents ?? [])
        setErr('')
      })
      .catch((e) => setErr(e.message))
    // Cost ledger is postgres-only — degrade silently in in-memory mode.
    api.costs().then(setCosts).catch(() => setCosts(null))
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [load])

  // SLA countdown: for each unanswered ticket compute hours until the next
  // threshold (breach threshold if already crossed shows as overdue).
  const slaRows = useMemo(() => {
    if (!sla) return []
    return sla.evaluations
      .filter((e) => e.unanswered)
      .map((e) => {
        const hit = e.breach as any
        const next = e.upcoming as any
        const threshold = hit?.unanswered_hours ?? next?.unanswered_hours ?? null
        const remaining = threshold != null ? threshold - e.age_hours : null
        return { ...e, threshold, remaining, breached: !!hit, tier: hit ?? next }
      })
      .sort((a, b) =>
        (a.breached === b.breached ? (a.remaining ?? 1e9) - (b.remaining ?? 1e9) : a.breached ? -1 : 1))
      .slice(0, 12)
  }, [sla])

  const laneMix = useMemo(() => {
    if (!summary) return []
    const counts: Record<string, number> = {}
    for (const t of summary.tickets) counts[t.lane ?? 'unknown'] = (counts[t.lane ?? 'unknown'] ?? 0) + 1
    const total = Math.max(1, summary.tickets.length)
    return Object.entries(counts).map(([lane, n]) => ({ lane, n, pct: n / total }))
      .sort((a, b) => b.n - a.n)
  }, [summary])

  const penalties = sla?.penalties_by_currency ?? {}
  const penaltyTotal = Object.entries(penalties)
    .map(([cur, amt]) => `${cur} ${Number(amt).toLocaleString('en-IN')}`)
    .join(' · ') || '—'

  // Chart series for the LLM cost card.
  const costByTenant = useMemo(() =>
    (costs?.by_tenant ?? []).map((t) => ({
      name: names[t.tenant_id] ?? t.tenant_id,
      cost: Number(t.cost_usd),
      calls: t.calls,
      tokens: t.input_tokens + t.output_tokens,
    })), [costs, names])
  const costByDay = useMemo(() =>
    [...(costs?.by_day ?? [])].reverse().map((d) => ({
      name: d.date.slice(5),
      cost: Number(d.cost_usd),
      calls: d.calls,
    })), [costs])
  const costByModel = useMemo(() =>
    (costs?.by_model ?? []).slice(0, 10).map((m) => ({
      name: `${m.model_id} · ${m.kind}`,
      cost: Number(m.cost_usd),
      calls: m.calls,
    })), [costs])

  // Incidents per tenant: declared-incident count + tickets linked.
  const incidentsByTenant = useMemo(() => {
    const by: Record<string, { incidents: number; tickets: number }> = {}
    for (const i of incidents) {
      const t = i.tenant_id ?? 'unknown'
      const slot = by[t] ??= { incidents: 0, tickets: 0 }
      slot.incidents += 1
      slot.tickets += i.ticket_count ?? (i.ticket_ids?.length ?? 0)
    }
    return Object.entries(by)
      .map(([tid, v]) => ({ name: names[tid] ?? tid, incidents: v.incidents, tickets: v.tickets }))
      .sort((a, b) => b.tickets - a.tickets)
  }, [incidents, names])

  const slaTbl = useTable(slaRows, {
    ticket_id: (e) => e.ticket_id,
    tenant: (e) => names[e.tenant_id] ?? e.tenant_id,
    queue: (e) => e.queue_id ?? '',
    age: (e) => e.age_hours,
    threshold: (e) => e.threshold ?? -1,
    remaining: (e) => e.remaining ?? 1e9,
    penalty: (e) => e.tier?.penalty_amount ?? '',
  })
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">Command center</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">
            Live ops view — auto-refreshes every 15s. Deflection, SLA exposure and incidents at a glance.
          </p>
        </div>
        <button onClick={load} className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 bg-white text-ink-500 hover:bg-ink-100">
          <RefreshCw size={14} />
        </button>
      </div>
      {err && <div className="rounded-lg border border-error-200 bg-error-50 px-4 py-2 text-[12px] text-error-600">{err}</div>}

      {/* KPI row */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi icon={Activity} label="Tickets" value={String(analytics?.total ?? '—')} sub={`${summary?.by_status?.open ?? 0} open`} />
        <Kpi icon={Bot} label="Automation rate" value={analytics ? pct(analytics.automation_rate) : '—'}
          sub={`${analytics?.bot_solved_count ?? 0} bot-solved · ${analytics?.human_solved_count ?? 0} human`} tone="text-emerald-500" />
        <Kpi icon={Timer} label="SLA breached" value={String(sla?.breached_count ?? '—')}
          sub={`${sla?.unanswered_count ?? 0} unanswered`} tone={(sla?.breached_count ?? 0) > 0 ? 'text-rose-500' : 'text-ink-400'} />
        <Kpi icon={IndianRupee} label="Penalty exposure" value={penaltyTotal} sub="unanswered tiers" tone="text-amber-500" />
        <Kpi icon={Radio} label="Active incidents" value={String(incidents.length)}
          sub={incidents[0] ? `${incidents[0].ticket_count} tickets · ${incidents[0].symptom_fingerprint}` : 'none declared'} tone={incidents.length ? 'text-rose-500' : 'text-ink-400'} />
        <Kpi icon={Users} label="Auto-routed" value={summary ? pct(summary.auto_routed / Math.max(1, summary.total)) : '—'}
          sub="deterministic rule match" />
      </div>

      {/* LLM usage & cost — charts */}
      {costs && (
        <Card title="LLM usage &amp; cost" className="p-4">
          <div className="mb-3 flex flex-wrap items-center gap-3 text-[12px] text-ink-500">
            <span>Total est. <b className="text-ink-800">${Number(costs.total_cost_usd).toFixed(4)}</b></span>
            <span>· {costs.by_tenant.reduce((s, t) => s + t.calls, 0)} calls</span>
            <span>· {costs.by_tenant.reduce((s, t) => s + t.input_tokens + t.output_tokens, 0).toLocaleString()} tokens</span>
            <span className="ml-auto">app-side estimate — authoritative bill is Cloud Billing export</span>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ink-400">Cost by tenant (USD)</div>
              <div className="h-44">
                {costByTenant.length === 0 ? <Empty text="No LLM calls yet." /> : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={costByTenant} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
                      <CartesianGrid strokeDasharray="3 4" stroke="#eef1f5" vertical={false} />
                      <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false}
                        tickFormatter={(v: string) => (v.length > 12 ? v.slice(0, 11) + '…' : v)} />
                      <YAxis tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false}
                        tickFormatter={(v: number) => `$${v.toFixed(3)}`} width={52} />
                      <Tooltip formatter={(v: any, n: any) => [`$${Number(v).toFixed(4)}`, n === 'cost' ? 'cost' : n]}
                        contentStyle={{ borderRadius: 10, border: '1px solid #e4e7ec', fontSize: 12 }} />
                      <Bar dataKey="cost" radius={[4, 4, 0, 0]} barSize={28}>
                        {costByTenant.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ink-400">Daily spend &amp; calls (14d)</div>
              <div className="h-44">
                {costByDay.length === 0 ? <Empty text="No LLM calls yet." /> : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={costByDay} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
                      <CartesianGrid strokeDasharray="3 4" stroke="#eef1f5" vertical={false} />
                      <XAxis dataKey="name" tick={{ fontSize: 9, fill: '#98a2b3' }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                      <YAxis yAxisId="cost" tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false}
                        tickFormatter={(v: number) => `$${v.toFixed(3)}`} width={52} />
                      <YAxis yAxisId="calls" orientation="right" tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false} width={30} allowDecimals={false} />
                      <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e4e7ec', fontSize: 12 }} />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      <Bar yAxisId="cost" dataKey="cost" name="cost (USD)" fill="#465fff" radius={[3, 3, 0, 0]} barSize={14} />
                      <Bar yAxisId="calls" dataKey="calls" name="calls" fill="#12b76a" radius={[3, 3, 0, 0]} barSize={14} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ink-400">By model · stage</div>
              <div className="h-44">
                {costByModel.length === 0 ? <Empty text="No LLM calls yet." /> : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={costByModel} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 8 }}>
                      <CartesianGrid strokeDasharray="3 4" stroke="#eef1f5" horizontal={false} />
                      <XAxis type="number" tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false}
                        tickFormatter={(v: number) => `$${v.toFixed(3)}`} />
                      <YAxis type="category" dataKey="name" width={130}
                        tick={{ fontSize: 10, fill: '#475467' }} tickLine={false} axisLine={false}
                        tickFormatter={(v: string) => (v.length > 20 ? v.slice(0, 19) + '…' : v)} />
                      <Tooltip formatter={(v: any) => [`$${Number(v).toFixed(4)}`, 'cost']}
                        contentStyle={{ borderRadius: 10, border: '1px solid #e4e7ec', fontSize: 12 }} />
                      <Bar dataKey="cost" radius={[0, 4, 4, 0]} barSize={14} fill="#7a5af8" />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[22rem_minmax(0,1fr)]">
        {/* Lane mix */}
        <Card title="Lane mix" className="p-4">
          {laneMix.length === 0 && <Empty text="Loading…" />}
          <div className="flex h-3 w-full overflow-hidden rounded-full bg-ink-100">
            {laneMix.map((l) => (
              <div key={l.lane}
                className={
                  l.lane === 'auto_answer' ? 'bg-emerald-500'
                  : l.lane === 'draft_for_agent' ? 'bg-brand-500'
                  : l.lane === 'human' ? 'bg-rose-400' : 'bg-ink-300'
                }
                style={{ width: `${l.pct * 100}%` }} title={`${l.lane}: ${l.n}`} />
            ))}
          </div>
          <div className="mt-3 flex flex-col gap-1.5">
            {laneMix.map((l) => (
              <div key={l.lane} className="flex items-center justify-between text-[12px]">
                <span className="flex items-center gap-2 text-ink-600">
                  <span className={`h-2 w-2 rounded-full ${
                    l.lane === 'auto_answer' ? 'bg-emerald-500'
                    : l.lane === 'draft_for_agent' ? 'bg-brand-500'
                    : l.lane === 'human' ? 'bg-rose-400' : 'bg-ink-300'}`} />
                  {l.lane.replaceAll('_', ' ')}
                </span>
                <span className="font-semibold tabular-nums text-ink-800">{l.n} · {pct(l.pct)}</span>
              </div>
            ))}
          </div>
        </Card>

        {/* Incidents */}
        <Card title="Incidents" className="p-4">
          {incidents.length === 0 && <Empty text="No incidents declared — clusters declare at 5 matching tickets." />}
          {incidentsByTenant.length > 0 && (
            <div className="mb-3">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ink-400">
                Incidents per tenant
              </div>
              <div className="h-36">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={incidentsByTenant} margin={{ top: 4, right: 8, bottom: 0, left: -18 }} barGap={2}>
                    <CartesianGrid strokeDasharray="3 4" stroke="#eef1f5" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false}
                      tickFormatter={(v: string) => (v.length > 12 ? v.slice(0, 11) + '…' : v)} />
                    <YAxis tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false} allowDecimals={false} width={30} />
                    <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e4e7ec', fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="incidents" name="incidents" fill="#f04438" radius={[3, 3, 0, 0]} barSize={14} />
                    <Bar dataKey="tickets" name="tickets linked" fill="#f79009" radius={[3, 3, 0, 0]} barSize={14} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
          <div className="flex flex-col gap-2">
            {incidents.map((i) => (
              <div key={i.symptom_fingerprint} className="flex items-start gap-3 rounded-lg border border-rose-200 bg-rose-50/60 px-3 py-2.5">
                <AlertTriangle size={16} className="mt-0.5 shrink-0 text-rose-500" />
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-[12px] font-semibold text-ink-800">{i.symptom_fingerprint}</div>
                  <div className="text-[11px] text-ink-500">
                    {i.ticket_count} tickets · declared {fmt(i.declared_at)} · tickets: {(i.ticket_ids ?? []).slice(0, 4).join(', ')}{(i.ticket_ids ?? []).length > 4 ? '…' : ''}
                  </div>
                </div>
                <Badge tone="expired">{i.ticket_count} linked</Badge>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* SLA countdown */}
      <Card title="SLA countdown — unanswered tickets by urgency" className="overflow-x-auto">
        {slaRows.length === 0 && <Empty text="No unanswered tickets — nothing approaching a penalty tier." />}
        {slaRows.length > 0 && (
          <table className="w-full min-w-[52rem] text-[13px]">
            <thead>
              <tr className="border-b border-ink-200 text-left text-ink-500">
                <Th t={slaTbl} k="ticket_id">Ticket</Th>
                <Th t={slaTbl} k="tenant">Tenant</Th>
                <Th t={slaTbl} k="queue">Queue</Th>
                <Th t={slaTbl} k="age">Age</Th>
                <Th t={slaTbl} k="threshold">Next threshold</Th>
                <Th t={slaTbl} k="remaining">Time left</Th>
                <Th t={slaTbl} k="penalty">Penalty</Th>
              </tr>
            </thead>
            <tbody>
              {slaTbl.visible.map((e) => (
                <tr key={`${e.tenant_id}/${e.ticket_id}`} className="border-b border-ink-50 hover:bg-ink-50/50">
                  <td className="px-3 py-2.5 font-mono text-[12px] font-semibold text-brand-600">{e.ticket_id}</td>
                  <td className="px-3 py-2.5 text-[12px] text-ink-600">{names[e.tenant_id] ?? e.tenant_id}</td>
                  <td className="px-3 py-2.5 text-[12px] text-ink-600">{e.queue_id ?? '—'}</td>
                  <td className="px-3 py-2.5 tabular-nums text-ink-800">{e.age_hours}h</td>
                  <td className="px-3 py-2.5 tabular-nums text-ink-600">
                    {e.threshold != null ? `${e.threshold}h` : '—'}
                    {e.breached && <span className="ml-1.5 text-[10px] font-bold text-rose-600">BREACHED</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    {e.breached
                      ? <Badge tone="expired">overdue</Badge>
                      : e.remaining != null
                        ? <Badge tone={e.remaining < 1 ? 'expired' : e.remaining < 4 ? 'in_progress' : 'scheduled'}>
                            {e.remaining < 0.1 ? '<6m' : `${e.remaining.toFixed(1)}h`}
                          </Badge>
                        : <span className="text-ink-400">—</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    {e.tier ? (
                      <Badge tone="expired">
                        {e.tier.currency} {e.tier.penalty_amount}
                      </Badge>
                    ) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
