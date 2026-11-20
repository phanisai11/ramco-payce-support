import { useId, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import type { Ticket } from '../api'

export function Card({ title, action, children, className = '' }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section className={`rounded-xl2 border border-ink-200 bg-white shadow-card ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-ink-100 px-5 py-3.5">
          <h3 className="text-sm font-semibold text-ink-800">{title}</h3>
          {action}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

const BADGE_TONES: Record<string, string> = {
  open: 'bg-info-50 text-info-600 border-brand-200',
  in_progress: 'bg-warning-50 text-warning-600 border-amber-200',
  pending_customer: 'bg-warning-50 text-warning-600 border-amber-200',
  resolved: 'bg-success-50 text-success-600 border-emerald-200',
  closed: 'bg-success-50 text-success-600 border-emerald-200',
  approved: 'bg-success-50 text-success-600 border-emerald-200',
  active: 'bg-success-50 text-success-600 border-emerald-200',
  scheduled: 'bg-brand-50 text-brand-700 border-brand-200',
  expired: 'bg-error-50 text-error-600 border-red-200',
  outdated: 'bg-ink-100 text-ink-500 border-ink-200',
  draft: 'bg-info-50 text-info-600 border-brand-200',
  inactive: 'bg-ink-100 text-ink-500 border-ink-200',
  auto_answer: 'bg-success-50 text-success-600 border-emerald-200',
  draft_for_agent: 'bg-info-50 text-info-600 border-brand-200',
  human_only: 'bg-error-50 text-error-600 border-red-200',
  p1: 'bg-error-50 text-error-600 border-red-200',
  p2: 'bg-orange-50 text-orange-600 border-orange-200',
  p3: 'bg-info-50 text-info-600 border-brand-200',
  p4: 'bg-ink-100 text-ink-500 border-ink-200',
}

export function Badge({ children, tone }: { children: ReactNode; tone?: string }) {
  const cls = tone ? (BADGE_TONES[tone] ?? 'bg-ink-100 text-ink-600 border-ink-200') : 'bg-ink-100 text-ink-600 border-ink-200'
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold capitalize ${cls}`}>
      {children}
    </span>
  )
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={status}>{status.replaceAll('_', ' ')}</Badge>
}

export function StatCard({ icon, label, value, tone = 'plain' }: {
  icon: ReactNode; label: string; value: ReactNode; tone?: 'plain' | 'ok' | 'warn' | 'bad'
}) {
  const tones = {
    plain: 'bg-brand-50 text-brand-500',
    ok: 'bg-success-50 text-success-600',
    warn: 'bg-warning-50 text-warning-600',
    bad: 'bg-error-50 text-error-600',
  }
  return (
    <div className="flex items-start gap-3.5 rounded-xl2 border border-ink-200 bg-white px-4 py-3.5 shadow-card">
      <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${tones[tone]}`}>{icon}</span>
      <div className="min-w-0">
        <div className="text-xl font-bold leading-tight tabular-nums text-ink-900">{value}</div>
        <div className="text-xs font-medium text-ink-500">{label}</div>
      </div>
    </div>
  )
}

export function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-ink-100 py-1.5 text-[13px] last:border-0">
      <span className="shrink-0 text-ink-500">{k}</span>
      <span className="min-w-0 break-words text-right font-medium text-ink-800">{children}</span>
    </div>
  )
}

export function TenantSelect({ value, onChange, tenants, names, includeAll = true, className = '' }: {
  value: string; onChange: (v: string) => void
  tenants: string[]; names: Record<string, string>
  includeAll?: boolean; className?: string
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className={`rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 text-[13px] text-ink-800 ${className}`}>
      {includeAll && <option value="">All tenants</option>}
      {tenants.map((t) => <option key={t} value={t}>{t} — {names[t] ?? t}</option>)}
    </select>
  )
}

export function Modal({ title, onClose, children, wide }: {
  title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4 backdrop-blur-[2px]"
      onClick={onClose}>
      <div
        className={`max-h-[90vh] w-full overflow-y-auto rounded-2xl border border-ink-200 bg-white p-5 shadow-pop ${wide ? 'max-w-3xl' : 'max-w-xl'}`}
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-[15px] font-bold text-ink-900">{title}</h3>
          <button onClick={onClose} className="rounded-lg p-1.5 text-ink-400 hover:bg-ink-100 hover:text-ink-700">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function Empty({ text }: { text: string }) {
  return <p className="py-10 text-center text-[13px] text-ink-400">{text}</p>
}

/* ------------------------------------------------------------------ */
/* Sortable + filterable table helpers                                 */
/* ------------------------------------------------------------------ */

export interface TableCtl<T = any> {
  visible: T[]
  sortKey: string
  dir: 1 | -1
  toggleSort: (k: string) => void
  filters: Record<string, string>
  setFilter: (k: string, v: string) => void
  optionsFor: (k: string) => string[]
}

/** Client-side sort (asc/desc on header click) + per-column text filter
 *  with a datalist of the column's distinct values. */
export function useTable<T>(
  rows: T[],
  accessors: Record<string, (r: T) => any> = {},
): TableCtl<T> {
  const [sortKey, setSortKey] = useState('')
  const [dir, setDir] = useState<1 | -1>(1)
  const [filters, setFilters] = useState<Record<string, string>>({})

  const val = (r: T, k: string) => {
    const acc = accessors[k]
    return acc ? acc(r) : (r as any)[k]
  }

  const visible = useMemo(() => {
    let out = rows
    for (const [k, v] of Object.entries(filters)) {
      if (!v) continue
      const needle = v.toLowerCase()
      out = out.filter((r) => String(val(r, k) ?? '').toLowerCase().includes(needle))
    }
    if (sortKey) {
      out = [...out].sort((a, b) => {
        const av = val(a, sortKey)
        const bv = val(b, sortKey)
        const cmp =
          typeof av === 'number' && typeof bv === 'number'
            ? av - bv
            : String(av ?? '').localeCompare(String(bv ?? ''))
        return cmp * dir
      })
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, filters, sortKey, dir])

  const toggleSort = (k: string) => {
    if (sortKey === k) setDir((d) => (d === 1 ? -1 : 1))
    else { setSortKey(k); setDir(1) }
  }
  const setFilter = (k: string, v: string) =>
    setFilters((f) => ({ ...f, [k]: v }))
  const optionsFor = (k: string) => {
    const seen = new Set<string>()
    for (const r of rows) {
      const s = String(val(r, k) ?? '')
      if (s && seen.size < 60) seen.add(s)
    }
    return [...seen].sort()
  }
  return { visible, sortKey, dir, toggleSort, filters, setFilter, optionsFor }
}

/** Sortable + filterable column header. Click label to cycle asc/desc;
 *  the small input filters rows (datalist offers the column's values). */
export function Th({ t, k, children, className = '' }: {
  t: TableCtl; k: string; children: ReactNode; className?: string
}) {
  const fid = useId()
  const active = t.sortKey === k
  return (
    <th className={className} onClick={() => t.toggleSort(k)}>
      <div className="flex cursor-pointer select-none items-center gap-1 whitespace-nowrap">
        {children}
        {active
          ? (t.dir === 1 ? <ArrowUp size={11} /> : <ArrowDown size={11} />)
          : <ArrowUpDown size={11} className="opacity-30" />}
      </div>
      <input
        value={t.filters[k] ?? ''}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => t.setFilter(k, e.target.value)}
        placeholder="filter"
        list={fid}
        className="mt-1 w-full min-w-[64px] rounded border border-ink-200 bg-white px-1 py-0.5 text-[10px] font-normal normal-case tracking-normal text-ink-600 outline-none focus:border-brand-400"
      />
      <datalist id={fid}>
        {t.optionsFor(k).map((o) => <option key={o} value={o} />)}
      </datalist>
    </th>
  )
}

export function ticketStatusClass(s: string) {
  return ['resolved', 'closed'].includes(s) ? 'bg-emerald-50 text-emerald-700' : ''
}

export type { Ticket }
