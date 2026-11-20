import { useEffect, useMemo, useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell, PieChart, Pie,
} from 'recharts'
import { Inbox, Timer, Bot, UserCheck, Gauge, AlertTriangle, IndianRupee, RefreshCw } from 'lucide-react'
import { api, type Analytics } from '../api'
import { StatCard, Card, Badge, TenantSelect } from '../components/ui'
import { useTicketDetail } from '../ticket'

const CHART_COLORS = ['#465fff', '#12b76a', '#f79009', '#f04438', '#7a5af8', '#0ba5ec', '#ee46bc', '#66c61c']

export function Home({ tenants, names }: { tenants: string[]; names: Record<string, string> }) {
  const [tenant, setTenant] = useState('')
  const [data, setData] = useState<Analytics | null>(null)
  const [err, setErr] = useState('')
  const { open } = useTicketDetail()

  // Overview defaults to the first tenant (per-tenant view); "All tenants" stays selectable.
  useEffect(() => {
    if (!tenant && tenants.length) setTenant(tenants[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenants])

  const load = () => api.analytics(tenant || undefined).then(setData).catch((e) => setErr(e.message))
  useEffect(() => { setData(null); load() }, [tenant]) // eslint-disable-line

  const dayData = useMemo(() =>
    (data?.by_day ?? []).map((d) => ({ name: d.date.slice(5), count: d.count })), [data])
  const queueData = useMemo(() =>
    Object.entries(data?.by_queue ?? {}).sort((a, b) => b[1] - a[1])
      .map(([name, count]) => ({ name, count })), [data])
  const splitData = useMemo(() => [
    { name: 'Bot-solved', value: data?.bot_solved_count ?? 0 },
    { name: 'Human-solved', value: data?.human_solved_count ?? 0 },
    { name: 'Open / in progress', value: data?.open_count ?? 0 },
  ].filter((s) => s.value > 0), [data])

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">Support overview</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">Ticket volume, channel mix and SLA exposure.</p>
        </div>
        <div className="flex items-center gap-2">
          <TenantSelect value={tenant} onChange={setTenant} tenants={tenants} names={names} />
          <button onClick={load} className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 bg-white text-ink-500 hover:bg-ink-100">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {err && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-error-600">{err}</div>}
      {!data && !err && <div className="py-16 text-center text-sm text-ink-400">Loading analytics…</div>}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
            <StatCard icon={<Inbox size={18} />} label="open tickets" value={data.open_count} />
            <StatCard icon={<Timer size={18} />} label="awaiting agent" value={data.pending_agent_count} tone={data.pending_agent_count ? 'warn' : 'plain'} />
            <StatCard icon={<Bot size={18} />} label="bot-solved" value={data.bot_solved_count} tone="ok" />
            <StatCard icon={<UserCheck size={18} />} label="human-solved" value={data.human_solved_count} />
            <StatCard icon={<AlertTriangle size={18} />} label="SLA breached" value={data.sla_breached_count} tone={data.sla_breached_count ? 'bad' : 'ok'} />
            {data.sla_attainment_pct != null && (
              <StatCard icon={<Gauge size={18} />} label="SLA attainment" value={`${data.sla_attainment_pct}%`}
                tone={data.sla_attainment_pct >= 90 ? 'ok' : data.sla_attainment_pct >= 70 ? 'warn' : 'bad'} />
            )}
            {Object.entries(data.sla_penalties_by_currency).map(([c, amt]) => (
              <StatCard key={c} icon={<IndianRupee size={18} />} label={`penalty exposure (${c})`} value={amt} tone="bad" />
            ))}
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card title="Ticket activity — last 14 days">
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={dayData} margin={{ top: 10, right: 12, bottom: 30, left: 8 }}>
                    <defs>
                      <linearGradient id="actFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#465fff" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="#465fff" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 4" stroke="#eef1f5" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false} interval={1}
                      label={{ value: 'Date (last 14 days)', position: 'insideBottom', offset: -2, style: { fontSize: 10, fill: '#98a2b3' } }} />
                    <YAxis tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false} allowDecimals={false} width={44}
                      label={{ value: 'Tickets', angle: -90, position: 'insideLeft', offset: 0, style: { fontSize: 10, fill: '#98a2b3' } }} />
                    <Tooltip
                      contentStyle={{ borderRadius: 10, border: '1px solid #e4e7ec', fontSize: 12, boxShadow: '0 4px 12px rgba(16,24,40,.08)' }}
                      labelStyle={{ color: '#667085' }} />
                    <Area type="monotone" dataKey="count" stroke="#465fff" strokeWidth={2.2}
                      fill="url(#actFill)" dot={{ r: 3, fill: '#fff', stroke: '#465fff', strokeWidth: 2 }} activeDot={{ r: 4 }} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card title="Resolution distribution">
              <div className="flex h-64 items-center">
                <ResponsiveContainer width="55%" height="100%">
                  <PieChart>
                    <Pie data={splitData} dataKey="value" nameKey="name" innerRadius={48} outerRadius={72}
                      paddingAngle={3} strokeWidth={0}>
                      {splitData.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                    </Pie>
                    <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e4e7ec', fontSize: 12 }} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="flex flex-1 flex-col gap-2">
                  {splitData.map((s, i) => (
                    <div key={s.name} className="flex items-center gap-2 text-[13px]">
                      <span className="h-2.5 w-2.5 rounded-full" style={{ background: CHART_COLORS[i % CHART_COLORS.length] }} />
                      <span className="text-ink-600">{s.name}</span>
                      <b className="ml-auto tabular-nums text-ink-900">
                        {data.total ? Math.round((s.value / (data.bot_solved_count + data.human_solved_count + data.open_count)) * 100) : 0}%
                      </b>
                    </div>
                  ))}
                </div>
              </div>
            </Card>

            <Card title="Top queues">
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={queueData} layout="vertical" margin={{ top: 10, right: 16, bottom: 30, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 4" stroke="#eef1f5" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false} allowDecimals={false}
                      label={{ value: 'Tickets', position: 'insideBottom', offset: -2, style: { fontSize: 10, fill: '#98a2b3' } }} />
                    <YAxis type="category" dataKey="name" width={150}
                      tick={{ fontSize: 11, fill: '#475467' }} tickLine={false} axisLine={false}
                      tickFormatter={(v: string) => (v.length > 20 ? v.slice(0, 19) + '…' : v)}
                      label={{ value: 'Queue', angle: -90, position: 'insideLeft', offset: -30, style: { fontSize: 10, fill: '#98a2b3' } }} />
                    <Tooltip cursor={{ fill: '#f9fafb' }} contentStyle={{ borderRadius: 10, border: '1px solid #e4e7ec', fontSize: 12 }} />
                    <Bar dataKey="count" radius={[0, 6, 6, 0]} barSize={16}>
                      {queueData.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card title="SLA risk & exposure">
              {data.sla_by_tenant.length === 0 ? (
                <p className="py-6 text-center text-[13px] text-ink-500">No SLA exposure — all tickets within thresholds.</p>
              ) : (
                <>
                  <div className="mb-4 h-52">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart
                        data={data.sla_by_tenant.map((t) => ({
                          name: names[t.tenant_id] ?? t.tenant_id,
                          unanswered: t.unanswered,
                          breached: t.breached,
                        }))}
                        margin={{ top: 10, right: 12, bottom: 46, left: 8 }}>
                        <CartesianGrid strokeDasharray="3 4" stroke="#eef1f5" vertical={false} />
                        <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false} interval={0} angle={-12} textAnchor="end" height={34}
                          label={{ value: 'Tenant', position: 'insideBottom', offset: -2, style: { fontSize: 10, fill: '#98a2b3' } }} />
                        <YAxis tick={{ fontSize: 10, fill: '#98a2b3' }} tickLine={false} axisLine={false} allowDecimals={false} width={40}
                          label={{ value: 'Tickets', angle: -90, position: 'insideLeft', offset: -2, style: { fontSize: 10, fill: '#98a2b3' } }} />
                        <Tooltip
                          cursor={{ fill: '#f9fafb' }}
                          contentStyle={{ borderRadius: 10, border: '1px solid #e4e7ec', fontSize: 12, boxShadow: '0 4px 12px rgba(16,24,40,.08)' }} />
                        <Bar dataKey="unanswered" name="Unanswered" fill="#f79009" radius={[4, 4, 0, 0]} barSize={18} />
                        <Bar dataKey="breached" name="SLA breached" fill="#f04438" radius={[4, 4, 0, 0]} barSize={18} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="flex flex-col gap-2.5 border-t border-ink-100 pt-3">
                  {data.sla_by_tenant.map((t) => (
                    <div key={t.tenant_id} className="flex items-center justify-between rounded-lg border border-ink-100 bg-ink-50/60 px-3.5 py-3">
                      <div>
                        <div className="text-[13px] font-semibold text-ink-800">{names[t.tenant_id] ?? t.tenant_id}</div>
                        <div className="mt-0.5 text-[11px] text-ink-500">{t.unanswered} unanswered · {t.breached} breached</div>
                      </div>
                      <div className="text-right">
                        {Object.keys(t.penalties ?? {}).length > 0 ? (
                          Object.entries(t.penalties).map(([c, a]) => (
                            <div key={c} className="text-[13px] font-bold tabular-nums text-error-600">{c} {a}</div>
                          ))
                        ) : (
                          <span className="text-[11px] font-medium text-success-600">within SLA</span>
                        )}
                      </div>
                    </div>
                  ))}
                  </div>
                </>
              )}
            </Card>
          </div>

          <Card title="Recent tickets">
            <div className="flex flex-col gap-2">
              {data.recent.length === 0 && <p className="py-4 text-center text-[13px] text-ink-500">No tickets yet — submit one from Intake.</p>}
              {data.recent.map((t) => (
                <button key={t.ticket_id} onClick={() => open({ tenant: t.tenant_id, ticket: t.ticket_id })}
                  className="flex items-center gap-3 rounded-lg border border-ink-100 px-3 py-2 text-left hover:border-brand-200 hover:bg-brand-25/50">
                  <span className="font-mono text-[13px] font-semibold text-brand-600">{t.ticket_id}</span>
                  <span className="text-[12px] text-ink-500">{names[t.tenant_id] ?? t.tenant_id} · {t.queue_id ?? 'unassigned'}</span>
                  {t.priority && <Badge tone={t.priority.toLowerCase()}>{t.priority}</Badge>}
                  <span className="ml-auto"><Badge tone={t.status}>{t.status.replaceAll('_', ' ')}</Badge></span>
                </button>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
