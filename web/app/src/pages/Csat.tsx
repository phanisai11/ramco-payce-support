import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, Star, ThumbsUp, MessageSquareHeart, TrendingUp, CalendarRange } from 'lucide-react'
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, type CsatQuarterRow, type CsatQuarters, type CsatReport, type CsatTenant } from '../api'
import { Card, Empty, StatCard, useTable, Th } from '../components/ui'

const SCORE_COLORS = ['#dc2626', '#ea580c', '#d97706', '#65a30d', '#16a34a']
const DECILE_COLORS = ['#16a34a', '#65a30d', '#84cc16', '#eab308', '#f59e0b', '#f97316', '#ef4444']

function decileColor(d: number | null) {
  if (d == null) return '#94a3b8'
  return DECILE_COLORS[Math.min(6, Math.max(0, Math.ceil(d / 2) - 1))]
}

function avgColor(v: number | null) {
  if (v == null) return '#94a3b8'
  if (v >= 4.5) return '#16a34a'
  if (v >= 4.0) return '#65a30d'
  if (v >= 3.5) return '#eab308'
  if (v >= 3.0) return '#f97316'
  return '#dc2626'
}

export function Csat({ names }: { tenants: string[]; names: Record<string, string> }) {
  const [report, setReport] = useState<CsatReport | null>(null)
  const [qyear, setQyear] = useState<number | ''>('')
  const [quarters, setQuarters] = useState<CsatQuarters | null>(null)

  const load = useCallback(() => {
    api.csat().then(setReport).catch(() => setReport(null))
  }, [])
  useEffect(() => { load() }, [load])

  const loadQuarters = useCallback(() => {
    api.csatQuarters(qyear === '' ? undefined : qyear)
      .then((q) => { setQuarters(q); if (qyear === '') setQyear(q.year) })
      .catch(() => setQuarters(null))
  }, [qyear])
  useEffect(() => { loadQuarters() }, [loadQuarters])

  const rows = report?.tenants ?? []
  const tname = (t: string) => names[t] ?? t

  const avgData = useMemo(() => rows.map((t) => ({
    name: tname(t.tenant_id), tenant: t.tenant_id, avg: t.avg_csat,
  })), [rows, names])

  const progData = useMemo(() => rows.map((t) => ({
    name: tname(t.tenant_id), delta: t.progress_delta ?? 0,
    has: t.progress_delta != null,
  })), [rows, names])

  const decileData = useMemo(() => rows.map((t) => ({
    name: tname(t.tenant_id), decile: t.decile ?? 10,
  })), [rows, names])

  // Overall 1–5 distribution across all tenants
  const distData = useMemo(() => {
    const totals: Record<string, number> = { '1': 0, '2': 0, '3': 0, '4': 0, '5': 0 }
    for (const t of rows) for (const [k, v] of Object.entries(t.distribution)) totals[k] = (totals[k] ?? 0) + v
    return [1, 2, 3, 4, 5].map((s) => ({ score: `${s}★`, count: totals[String(s)] }))
  }, [rows])

  const qrows = quarters?.by_tenant ?? []
  const qtbl = useTable(qrows, {
    tenant: (r: CsatQuarterRow) => r.tenant_id ? (names[r.tenant_id] ?? r.tenant_id) : 'All tenants',
    quarter: (r) => r.quarter,
    tickets: (r) => r.tickets,
    rated: (r) => r.rated,
    response: (r) => r.response_rate,
    avg: (r) => r.avg_csat ?? -1,
    dist: (r) => [1, 2, 3, 4, 5].map((s) => r.distribution[String(s)] ?? 0).join('/'),
  })

  const tbl = useTable(rows, {
    tenant: (t: CsatTenant) => tname(t.tenant_id),
    tickets: (t) => t.total_tickets,
    rated: (t) => t.rated,
    response: (t) => t.response_rate,
    avg: (t) => t.avg_csat ?? -1,
    progress: (t) => t.progress_delta ?? -99,
    decile: (t) => t.decile ?? 99,
    dist: (t) => [1, 2, 3, 4, 5].map((s) => t.distribution[String(s)] ?? 0).join('/'),
  })

  const TopTable = ({ title, list, metric }: {
    title: string; list: CsatTenant[]; metric: 'avg_csat' | 'progress_delta'
  }) => (
    <Card title={title}>
      {list.length === 0 ? <Empty text="Not enough rated tickets yet" /> : (
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-ink-100 text-left text-[11px] uppercase tracking-wide text-ink-400">
              <th className="py-2 pr-3">#</th><th className="py-2 pr-3">Tenant</th>
              <th className="py-2 pr-3 text-right">{metric === 'avg_csat' ? 'Avg CSAT' : 'Progress Δ'}</th>
              <th className="py-2 text-right">Rated</th>
            </tr>
          </thead>
          <tbody>
            {list.map((t, i) => (
              <tr key={t.tenant_id} className="border-b border-ink-50 last:border-0">
                <td className="py-2 pr-3 text-ink-400">{i + 1}</td>
                <td className="py-2 pr-3 font-medium text-ink-800">{tname(t.tenant_id)}</td>
                <td className="py-2 pr-3 text-right font-semibold"
                  style={{ color: metric === 'avg_csat' ? avgColor(t.avg_csat) : (t.progress_delta ?? 0) >= 0 ? '#16a34a' : '#dc2626' }}>
                  {metric === 'avg_csat' ? t.avg_csat?.toFixed(2) ?? '—' : `${(t.progress_delta ?? 0) >= 0 ? '+' : ''}${t.progress_delta?.toFixed(3) ?? '—'}`}
                </td>
                <td className="py-2 text-right text-ink-500">{t.rated}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">CSAT</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">
            Post-resolution customer ratings per tenant — average, response rate,
            1–5 distribution, decile rank, and progress trend.
          </p>
        </div>
        <button onClick={load}
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-[13px] font-medium text-ink-600 hover:bg-ink-50">
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard icon={<Star size={18} />} label="Overall avg CSAT" tone="plain"
          value={report?.overall.avg_csat != null ? report.overall.avg_csat.toFixed(2) : '—'} />
        <StatCard icon={<MessageSquareHeart size={18} />} label="Ratings collected" tone="plain"
          value={String(report?.overall.rated ?? 0)} />
        <StatCard icon={<ThumbsUp size={18} />} label="Response coverage"
          value={report ? `${Math.round((report.overall.rated / Math.max(report.overall.total_tickets, 1)) * 100)}%` : '—'} />
        <StatCard icon={<TrendingUp size={18} />} label="Tenants tracked" tone="plain"
          value={String(rows.filter((t) => t.avg_csat != null).length)} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Avg CSAT by tenant" className="lg:col-span-1">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={avgData} margin={{ left: -18, right: 8, top: 4, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-15} textAnchor="end" height={50} />
                <YAxis domain={[0, 5]} tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v: any) => (v == null ? '—' : Number(v).toFixed(2))} />
                <Bar dataKey="avg" radius={[4, 4, 0, 0]}>
                  {avgData.map((d) => <Cell key={d.tenant} fill={avgColor(d.avg)} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="CSAT progress (recent vs earlier half)" className="lg:col-span-1">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={progData} margin={{ left: -18, right: 8, top: 4, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-15} textAnchor="end" height={50} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v: any) => `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(3)}`} />
                <Bar dataKey="delta" radius={[4, 4, 0, 0]}>
                  {progData.map((d) => <Cell key={d.name} fill={d.delta >= 0 ? '#16a34a' : '#dc2626'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Decile rank (1 = top)" className="lg:col-span-1">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={decileData} margin={{ left: -18, right: 8, top: 4, bottom: 0 }} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" horizontal={false} />
                <XAxis type="number" domain={[0, 10]} reversed tick={{ fontSize: 11 }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={90} />
                <Tooltip />
                <Bar dataKey="decile" radius={[0, 4, 4, 0]}>
                  {decileData.map((d) => {
                    const t = rows.find((r) => tname(r.tenant_id) === d.name)
                    return <Cell key={d.name} fill={decileColor(t?.decile ?? null)} />
                  })}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Rating distribution (all tenants)" className="lg:col-span-1">
          <div className="h-52">
            <ResponsiveContainer>
              <BarChart data={distData} margin={{ left: -18, right: 8, top: 4, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                <XAxis dataKey="score" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {distData.map((d, i) => <Cell key={d.score} fill={SCORE_COLORS[i]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <div className="lg:col-span-2 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TopTable title="Top 5 — highest avg CSAT" list={report?.top_by_score ?? []} metric="avg_csat" />
          <TopTable title="Top 5 — most improved" list={report?.top_by_progress ?? []} metric="progress_delta" />
        </div>
      </div>

      <Card title="Quarterly CSAT"
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
          {(quarters?.quarters ?? []).map((q) => (
            <div key={q.quarter} className="rounded-xl border border-ink-200 bg-ink-50/60 px-3.5 py-3">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">
                {q.quarter} {q.year}
              </div>
              <div className="mt-1 text-lg font-bold tabular-nums"
                style={{ color: avgColor(q.avg_csat) }}>
                {q.avg_csat != null ? q.avg_csat.toFixed(2) : '—'}
                <span className="ml-1 text-[11px] font-medium text-ink-400">avg</span>
              </div>
              <div className="mt-0.5 text-[11px] text-ink-500">
                {q.rated} rated · {q.tickets} tickets · {(q.response_rate * 100).toFixed(0)}% response
              </div>
            </div>
          ))}
        </div>
        {qtbl.visible.length === 0 ? (
          <Empty text={`No CSAT data in ${qyear || 'selected year'}.`} />
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-ink-200 text-left">
                <Th t={qtbl} k="tenant">Tenant</Th>
                <Th t={qtbl} k="quarter">Quarter</Th>
                <Th t={qtbl} k="tickets">Tickets</Th>
                <Th t={qtbl} k="rated">Rated</Th>
                <Th t={qtbl} k="response">Response %</Th>
                <Th t={qtbl} k="avg">Avg CSAT</Th>
                <Th t={qtbl} k="dist">Dist 1–5</Th>
              </tr>
            </thead>
            <tbody>
              {qtbl.visible.map((r, i) => (
                <tr key={`${r.tenant_id}-${r.quarter}-${i}`} className="border-b border-ink-50 last:border-0 hover:bg-ink-50/60">
                  <td className="py-2 pr-3 font-medium text-ink-800">
                    {r.tenant_id ? (names[r.tenant_id] ?? r.tenant_id) : 'All tenants'}
                  </td>
                  <td className="py-2 pr-3"><span className="rounded-full bg-ink-100 px-2 py-0.5 text-[11px] font-semibold text-ink-600">{r.quarter}</span></td>
                  <td className="py-2 pr-3 text-ink-500">{r.tickets}</td>
                  <td className="py-2 pr-3 text-ink-500">{r.rated}</td>
                  <td className="py-2 pr-3 text-ink-500">{(r.response_rate * 100).toFixed(1)}%</td>
                  <td className="py-2 pr-3 font-semibold" style={{ color: avgColor(r.avg_csat) }}>
                    {r.avg_csat != null ? r.avg_csat.toFixed(2) : '—'}
                  </td>
                  <td className="py-2 font-mono text-[11px] text-ink-500">
                    {[1, 2, 3, 4, 5].map((s) => r.distribution[String(s)] ?? 0).join(' / ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="All tenants" className="!p-0">
        <div className="overflow-x-auto p-4">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-ink-100 text-left">
                <Th t={tbl} k="tenant">Tenant</Th>
                <Th t={tbl} k="tickets">Tickets</Th>
                <Th t={tbl} k="rated">Rated</Th>
                <Th t={tbl} k="response">Response %</Th>
                <Th t={tbl} k="avg">Avg CSAT</Th>
                <Th t={tbl} k="progress">Progress Δ</Th>
                <Th t={tbl} k="decile">Decile</Th>
                <Th t={tbl} k="dist">Dist 1–5</Th>
              </tr>
            </thead>
            <tbody>
              {tbl.visible.map((t: CsatTenant) => (
                <tr key={t.tenant_id} className="border-b border-ink-50 last:border-0 hover:bg-ink-50/60">
                  <td className="py-2 pr-3 font-medium text-ink-800">{tname(t.tenant_id)}</td>
                  <td className="py-2 pr-3 text-ink-500">{t.total_tickets}</td>
                  <td className="py-2 pr-3 text-ink-500">{t.rated}</td>
                  <td className="py-2 pr-3 text-ink-500">{(t.response_rate * 100).toFixed(1)}%</td>
                  <td className="py-2 pr-3 font-semibold" style={{ color: avgColor(t.avg_csat) }}>
                    {t.avg_csat != null ? t.avg_csat.toFixed(2) : '—'}
                  </td>
                  <td className="py-2 pr-3 font-medium"
                    style={{ color: (t.progress_delta ?? 0) >= 0 ? '#16a34a' : '#dc2626' }}>
                    {t.progress_delta != null ? `${t.progress_delta >= 0 ? '+' : ''}${t.progress_delta.toFixed(3)}` : '—'}
                  </td>
                  <td className="py-2 pr-3">
                    {t.decile != null ? (
                      <span className="rounded-full px-2 py-0.5 text-[11px] font-semibold text-white"
                        style={{ background: decileColor(t.decile) }}>D{t.decile}</span>
                    ) : '—'}
                  </td>
                  <td className="py-2 font-mono text-[11px] text-ink-500">
                    {[1, 2, 3, 4, 5].map((s) => t.distribution[String(s)] ?? 0).join(' / ')}
                  </td>
                </tr>
              ))}
              {tbl.visible.length === 0 && (
                <tr><td colSpan={8} className="py-8 text-center text-ink-400">No CSAT data yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
