import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, Pencil, Plus, Trash2, ChevronDown, Check } from 'lucide-react'
import { api, type SlaRule } from '../api'
import { Badge, Card, Empty, Modal, useTable, Th } from '../components/ui'

const STATUS_OPTIONS = ['open', 'in_progress', 'pending_customer', 'resolved', 'closed']
const LIFECYCLE = ['approved', 'in_progress', 'outdated', 'draft', 'expired']

const inputCls = 'w-full rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 font-mono text-[12px] outline-none focus:border-brand-500'
const labelCls = 'mb-1 block text-[11px] font-semibold text-ink-500'

interface RuleForm {
  scope: 'tenant' | 'region'
  tenant_ids: string[]
  region: string
  unanswered_hours: string
  penalty_amount: string
  currency: string
  valid_from: string
  valid_to: string
  status: string
  applies_to_statuses: string
  applies_to_priorities: string
  description: string
}

const EMPTY_FORM: RuleForm = {
  scope: 'tenant',
  tenant_ids: [],
  region: 'IN',
  unanswered_hours: '',
  penalty_amount: '',
  currency: 'INR',
  valid_from: '',
  valid_to: '',
  status: 'approved',
  applies_to_statuses: 'open, pending_customer',
  applies_to_priorities: '',
  description: '',
}

function formToPayloads(f: RuleForm): any[] {
  const base: any = {
    unanswered_hours: Number(f.unanswered_hours),
    penalty_amount: f.penalty_amount.trim(),
    currency: f.currency.trim().toUpperCase(),
    status: f.status,
    applies_to_statuses: f.applies_to_statuses
      .split(',').map((s) => s.trim()).filter(Boolean),
    applies_to_priorities: f.applies_to_priorities
      .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
  }
  if (f.valid_from) base.valid_from = f.valid_from
  if (f.valid_to) base.valid_to = f.valid_to
  if (f.description.trim()) base.description = f.description.trim()
  if (f.scope === 'region' && f.region) {
    return [{ ...base, region: f.region }]
  }
  // One rule row per selected tenant — tenant_id is a single FK column.
  return f.tenant_ids.map((tid) => ({ ...base, tenant_id: tid }))
}

function TenantMultiSelect({ tenants, names, selected, onChange }: {
  tenants: string[]; names: Record<string, string>
  selected: string[]; onChange: (ids: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])
  const toggle = (t: string) =>
    onChange(selected.includes(t) ? selected.filter((x) => x !== t) : [...selected, t])
  return (
    <div className="relative block sm:col-span-2" ref={ref}>
      <span className={labelCls}>Tenants (select one or more)</span>
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 text-left font-mono text-[12px] outline-none focus:border-brand-500">
        <span className={selected.length ? 'text-ink-800' : 'text-ink-400'}>
          {selected.length === 0
            ? '— select tenants —'
            : selected.length === tenants.length
              ? `All tenants (${selected.length})`
              : `${selected.length} tenant${selected.length > 1 ? 's' : ''} selected`}
        </span>
        <ChevronDown size={14} className={`text-ink-400 transition ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-ink-200 bg-white py-1 shadow-lg">
          <button type="button"
            onClick={() => onChange(selected.length === tenants.length ? [] : [...tenants])}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] font-semibold text-brand-600 hover:bg-ink-50">
            <span className="w-4">{selected.length === tenants.length && tenants.length > 0 ? <Check size={13} /> : null}</span>
            {selected.length === tenants.length && tenants.length > 0 ? 'Clear all' : 'Select all'}
          </button>
          {tenants.map((t) => {
            const on = selected.includes(t)
            return (
              <button key={t} type="button" onClick={() => toggle(t)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-ink-700 hover:bg-ink-50">
                <span className={`flex h-4 w-4 items-center justify-center rounded border ${on ? 'border-brand-500 bg-brand-500 text-white' : 'border-ink-300'}`}>
                  {on && <Check size={11} />}
                </span>
                {names[t] ?? t} <span className="font-mono text-[11px] text-ink-400">({t})</span>
              </button>
            )
          })}
        </div>
      )}
      {selected.length > 1 && !open && (
        <p className="mt-1 text-[11px] text-ink-400">
          Creates {selected.length} rules — one per tenant.
        </p>
      )}
    </div>
  )
}

function RuleFormFields({ form, setForm, tenants, names, isEdit }: {
  form: RuleForm; setForm: (f: RuleForm) => void
  tenants: string[]; names: Record<string, string>; isEdit?: boolean
}) {
  const set = (k: keyof RuleForm, v: string) => setForm({ ...form, [k]: v })
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {!isEdit && (
        <>
          <label className="block">
            <span className={labelCls}>Scope</span>
            <select value={form.scope} onChange={(e) => set('scope', e.target.value as RuleForm['scope'])} className={inputCls}>
              <option value="tenant">Specific tenant</option>
              <option value="region">Region fallback (all tenants in region)</option>
            </select>
          </label>
          {form.scope === 'tenant' ? (
            <TenantMultiSelect
              tenants={tenants} names={names}
              selected={form.tenant_ids}
              onChange={(ids) => setForm({ ...form, tenant_ids: ids })}
            />
          ) : (
            <label className="block">
              <span className={labelCls}>Region</span>
              <select value={form.region} onChange={(e) => set('region', e.target.value)} className={inputCls}>
                <option value="IN">IN — all India tenants</option>
                <option value="UAE">UAE — all UAE tenants</option>
              </select>
            </label>
          )}
        </>
      )}
      <label className="block">
        <span className={labelCls}>Unanswered hours until penalty</span>
        <input value={form.unanswered_hours} onChange={(e) => set('unanswered_hours', e.target.value)} type="number" min={0} step="0.5" className={inputCls} placeholder="e.g. 4" />
      </label>
      <label className="block">
        <span className={labelCls}>Penalty amount</span>
        <input value={form.penalty_amount} onChange={(e) => set('penalty_amount', e.target.value)} className={inputCls} placeholder="e.g. 500.00" />
      </label>
      <label className="block">
        <span className={labelCls}>Currency</span>
        <input value={form.currency} onChange={(e) => set('currency', e.target.value)} className={inputCls} placeholder="INR / AED" />
      </label>
      <label className="block">
        <span className={labelCls}>Lifecycle status</span>
        <select value={form.status} onChange={(e) => set('status', e.target.value)} className={inputCls}>
          {LIFECYCLE.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <label className="block">
        <span className={labelCls}>Valid from</span>
        <input value={form.valid_from} onChange={(e) => set('valid_from', e.target.value)} type="date" className={inputCls} />
      </label>
      <label className="block">
        <span className={labelCls}>Valid to</span>
        <input value={form.valid_to} onChange={(e) => set('valid_to', e.target.value)} type="date" className={inputCls} />
      </label>
      <label className="block sm:col-span-2">
        <span className={labelCls}>Applies to statuses (comma-separated)</span>
        <input value={form.applies_to_statuses} onChange={(e) => set('applies_to_statuses', e.target.value)} className={inputCls} placeholder={STATUS_OPTIONS.join(', ')} />
      </label>
      <label className="block">
        <span className={labelCls}>Applies to priorities (blank = all)</span>
        <input value={form.applies_to_priorities} onChange={(e) => set('applies_to_priorities', e.target.value)} className={inputCls} placeholder="p1, p2" />
      </label>
      <label className="block sm:col-span-2">
        <span className={labelCls}>Description</span>
        <input value={form.description} onChange={(e) => set('description', e.target.value)} className={inputCls} placeholder="What this tier covers" />
      </label>
    </div>
  )
}

export function SlaRules({ tenants, names }: { tenants: string[]; names: Record<string, string> }) {
  const [data, setData] = useState<{ sla_policy_version: string; rules: SlaRule[] } | null>(null)
  const [editing, setEditing] = useState<SlaRule | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState<RuleForm>(EMPTY_FORM)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => api.slaRules().then(setData).catch(() => setData(null)), [])
  useEffect(() => { load() }, [load])

  const effTone = (s?: string) =>
    s === 'approved' || s === 'active' ? 'approved'
    : s === 'in_progress' || s === 'scheduled' ? 'scheduled' : 'expired'

  const tbl = useTable(data?.rules ?? [], {
    rule: (r) => r.id,
    scope: (r) => r.tenant_id ? `tenant ${names[r.tenant_id] ?? r.tenant_id}` : r.region ? `region ${r.region}` : 'global',
    threshold: (r) => r.unanswered_hours,
    penalty: (r) => `${r.currency} ${r.penalty_amount}`,
    applies: (r) => [...(r.applies_to_statuses ?? []), ...(r.applies_to_priorities ?? [])].join(' '),
    validity: (r) => `${r.valid_from ?? ''} ${r.valid_to ?? ''}`.trim() || 'always',
    status: (r) => r.effective_status ?? r.status,
  })

  const saveNew = async () => {
    setBusy(true); setMsg('')
    const payloads = formToPayloads(form)
    try {
      for (const p of payloads) await api.createSlaRule(p)
      setAdding(false); setForm(EMPTY_FORM); setMsg('')
      await load()
    } catch (e: any) { setMsg(`Error: ${e.message}`) }
    setBusy(false)
  }

  const saveEdit = async () => {
    if (!editing) return
    setBusy(true); setMsg('')
    const payload: any = {}
    const f = form
    if (f.unanswered_hours) payload.unanswered_hours = Number(f.unanswered_hours)
    if (f.penalty_amount.trim()) payload.penalty_amount = f.penalty_amount.trim()
    if (f.currency.trim()) payload.currency = f.currency.trim().toUpperCase()
    if (f.status) payload.status = f.status
    if (f.applies_to_statuses.trim()) payload.applies_to_statuses = f.applies_to_statuses.split(',').map((s) => s.trim()).filter(Boolean)
    if (f.applies_to_priorities.trim()) payload.applies_to_priorities = f.applies_to_priorities.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean)
    if (f.valid_from) payload.valid_from = f.valid_from
    if (f.valid_to) payload.valid_to = f.valid_to
    if (f.description.trim()) payload.description = f.description.trim()
    try {
      await api.updateSlaRule(editing.id, payload)
      setEditing(null); await load()
    } catch (e: any) { setMsg(`Error: ${e.message}`) }
    setBusy(false)
  }

  const remove = async (r: SlaRule) => {
    if (!window.confirm(`Soft-delete SLA rule ${r.id}? It stays in the DB for audit but stops applying immediately.`)) return
    setBusy(true); setMsg('')
    try {
      await api.deleteSlaRule(r.id)
      await load()
    } catch (e: any) { setMsg(`Error: ${e.message}`) }
    setBusy(false)
  }

  const openEdit = (r: SlaRule) => {
    setMsg('')
    setEditing(r)
    setForm({
      ...EMPTY_FORM,
      unanswered_hours: String(r.unanswered_hours ?? ''),
      penalty_amount: String(r.penalty_amount ?? ''),
      currency: r.currency ?? 'INR',
      status: r.status ?? 'approved',
      valid_from: r.valid_from ?? '',
      valid_to: r.valid_to ?? '',
      applies_to_statuses: (r.applies_to_statuses ?? []).join(', '),
      applies_to_priorities: (r.applies_to_priorities ?? []).join(', '),
      description: r.description ?? '',
    })
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">SLA rules</h2>
          <p className="mt-0.5 max-w-2xl text-[13px] text-ink-500">
            Unanswered-time thresholds and penalties, stored in Postgres (<code className="rounded bg-ink-100 px-1 text-[11px]">app.sla_rule</code>).
            {' '}<b>Tenant-specific rules win</b>; when a tenant has none, the <b>region fallback</b> rule applies —
            one IN rule covers every IN tenant. These rules are also sent to the LLM as reference during ticket analysis.
            {data && <> Policy <code className="rounded bg-ink-100 px-1 text-[11px]">{data.sla_policy_version}</code>.</>}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={load} className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 bg-white text-ink-500 hover:bg-ink-100">
            <RefreshCw size={14} />
          </button>
          <button onClick={() => { setForm(EMPTY_FORM); setMsg(''); setAdding(true) }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-500 px-3.5 py-2 text-xs font-semibold text-white hover:bg-brand-600">
            <Plus size={14} /> Add SLA rule
          </button>
        </div>
      </div>

      {msg && !adding && !editing && <div className="rounded-lg border border-error-200 bg-error-50 px-4 py-2 text-[12px] text-error-600">{msg}</div>}

      <Card title="Rules" className="overflow-x-auto">
        {!data && <Empty text="Loading…" />}
        {data && (
          <table className="w-full min-w-[56rem] text-[13px]">
            <thead>
              <tr className="border-b border-ink-200 text-left text-ink-500">
                <Th t={tbl} k="rule" className="px-3 py-3">Rule</Th>
                <Th t={tbl} k="scope" className="px-3 py-3">Scope</Th>
                <Th t={tbl} k="threshold" className="px-3 py-3">Threshold</Th>
                <Th t={tbl} k="penalty" className="px-3 py-3">Penalty</Th>
                <Th t={tbl} k="applies" className="px-3 py-3">Applies to</Th>
                <Th t={tbl} k="validity" className="px-3 py-3">Validity</Th>
                <Th t={tbl} k="status" className="px-3 py-3">Status</Th>
                <th className="px-3 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {tbl.visible.map((r) => (
                <tr key={r.id} className="border-b border-ink-50 align-top hover:bg-ink-50/50">
                  <td className="px-3 py-3">
                    <code className="font-mono text-[12px] font-semibold text-brand-600">{r.id}</code>
                    {r.description ? <div className="mt-1 max-w-[16rem] text-[11px] text-ink-400">{r.description}</div> : null}
                  </td>
                  <td className="px-3 py-3 text-[12px] text-ink-600">
                    {r.tenant_id ? (
                      <Badge tone="in_progress">tenant · {names[r.tenant_id] ?? r.tenant_id}</Badge>
                    ) : r.region ? (
                      <Badge tone="scheduled">region {r.region} · all {r.region} tenants</Badge>
                    ) : (
                      <Badge tone="outdated">global</Badge>
                    )}
                  </td>
                  <td className="px-3 py-3 font-semibold tabular-nums text-ink-800">{r.unanswered_hours}h</td>
                  <td className="px-3 py-3"><Badge tone="expired">{r.currency} {r.penalty_amount}</Badge></td>
                  <td className="px-3 py-3 text-[11px] text-ink-500">
                    {(r.applies_to_statuses ?? []).join(', ') || 'all'}
                    {(r.applies_to_priorities ?? []).length > 0 && (
                      <span className="ml-1 text-brand-600">· {(r.applies_to_priorities ?? []).join(', ')}</span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-[11px] tabular-nums text-ink-500">
                    {r.valid_from || r.valid_to ? <>{r.valid_from ?? '…'} → {r.valid_to ?? '…'}</> : 'always'}
                  </td>
                  <td className="px-3 py-3"><Badge tone={effTone(r.effective_status ?? r.status)}>{r.effective_status ?? r.status}</Badge></td>
                  <td className="px-3 py-3">
                    <div className="flex gap-1.5">
                      <button onClick={() => openEdit(r)}
                        className="rounded-lg border border-ink-200 p-1.5 text-ink-500 hover:bg-ink-100 hover:text-brand-500" title="Edit rule">
                        <Pencil size={13} />
                      </button>
                      <button onClick={() => remove(r)}
                        className="rounded-lg border border-ink-200 p-1.5 text-ink-500 hover:bg-error-50 hover:text-error-600" title="Delete rule (soft delete — kept for audit)">
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Add rule modal */}
      {adding && (
      <Modal onClose={() => setAdding(false)} title="Add SLA rule" wide>
        <p className="mb-3 text-[12px] text-ink-500">
          Tenant rules apply only to that tenant. Region rules are the shared fallback —
          every tenant in the region without its own rule uses them.
        </p>
        <RuleFormFields form={form} setForm={setForm} tenants={tenants} names={names} />
        {msg && <div className="mt-3 rounded-lg border border-error-200 bg-error-50 px-3 py-2 text-[12px] text-error-600">{msg}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={() => setAdding(false)} className="rounded-lg border border-ink-200 px-3.5 py-1.5 text-xs font-semibold text-ink-600 hover:bg-ink-100">Cancel</button>
          <button onClick={saveNew} disabled={busy || !form.unanswered_hours || !form.penalty_amount || (form.scope === 'tenant' && form.tenant_ids.length === 0)}
            className="rounded-lg bg-brand-500 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
            {busy ? 'Saving…' : 'Create rule'}
          </button>
        </div>
      </Modal>
      )}

      {/* Edit rule modal */}
      {editing && (
      <Modal onClose={() => setEditing(null)} title={`Edit SLA rule ${editing.id}`} wide>
        <p className="mb-3 text-[12px] text-ink-500">
          Scope (tenant/region) is fixed — to change it, create a new rule and expire this one.
        </p>
        <RuleFormFields form={form} setForm={setForm} tenants={tenants} names={names} isEdit />
        {msg && <div className="mt-3 rounded-lg border border-error-200 bg-error-50 px-3 py-2 text-[12px] text-error-600">{msg}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={() => setEditing(null)} className="rounded-lg border border-ink-200 px-3.5 py-1.5 text-xs font-semibold text-ink-600 hover:bg-ink-100">Cancel</button>
          <button onClick={saveEdit} disabled={busy}
            className="rounded-lg bg-brand-500 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
            {busy ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </Modal>
      )}
    </div>
  )
}
