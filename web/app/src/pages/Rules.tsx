import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Pencil, Plus } from 'lucide-react'
import { api, type Policy, type PolicyRule, type TenantContext } from '../api'
import { Badge, Card, Empty, Modal, useTable, Th } from '../components/ui'

const inputCls = 'w-full rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 font-mono text-[12px] outline-none focus:border-brand-500'
const lblCls = 'mb-1 block text-[11px] font-semibold text-ink-500'

/* ---------- routing rule edit (modal) ---------- */

const RULE_FIELDS = [
  { key: 'pattern', label: 'Pattern (regex)', csv: false },
  { key: 'signals', label: 'Signals (comma-sep)', csv: true },
  { key: 'reason_codes', label: 'Reason codes (comma-sep)', csv: true },
  { key: 'queue_id', label: 'Queue', csv: false },
  { key: 'lane', label: 'Lane', csv: false },
  { key: 'tenant_id', label: 'Tenant (blank = all)', csv: false, nullable: true },
  { key: 'region', label: 'Region (blank = all)', csv: false, nullable: true },
  { key: 'valid_from', label: 'Valid from (ISO date)', csv: false, nullable: true },
  { key: 'valid_to', label: 'Valid to (ISO date)', csv: false, nullable: true },
] as const

function RuleEditModal({ rule, onSaved, onClose }: {
  rule: PolicyRule; onSaved: (r: PolicyRule) => void; onClose: () => void
}) {
  const [form, setForm] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    setForm({
      pattern: rule.pattern ?? '',
      signals: (rule.signals ?? []).join(', '),
      reason_codes: (rule.reason_codes ?? []).join(', '),
      queue_id: rule.queue_id ?? '',
      lane: rule.lane ?? '',
      tenant_id: rule.tenant_id ?? '',
      region: rule.region ?? '',
      valid_from: rule.valid_from ?? '',
      valid_to: rule.valid_to ?? '',
    })
  }, [rule])

  const save = async () => {
    setBusy(true); setMsg('')
    const payload: any = {}
    for (const f of RULE_FIELDS) {
      const v = (form[f.key] ?? '').trim()
      if (v === '') {
        if ('nullable' in f && f.nullable) payload[f.key] = null
        continue
      }
      payload[f.key] = f.csv ? v.split(',').map((s) => s.trim()).filter(Boolean) : v
    }
    try {
      onSaved(await api.updateRule(rule.id, payload))
      onClose()
    } catch (e: any) { setMsg(`Error: ${e.message}`) }
    setBusy(false)
  }

  return (
    <Modal title={`Edit routing rule — ${rule.id}`} onClose={onClose} wide>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {RULE_FIELDS.map((f) => (
          <label key={f.key} className="block">
            <span className={lblCls}>{f.label}</span>
            <input value={form[f.key] ?? ''} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
              className={inputCls} />
          </label>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button onClick={save} disabled={busy}
          className="rounded-lg bg-brand-500 px-4 py-2 text-[13px] font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
          {busy ? 'Saving…' : 'Save rule'}
        </button>
        {msg && <span className="text-[12px] text-error-600">{msg}</span>}
        <span className="ml-auto text-[11px] text-ink-400">Persists to approved.yaml — live for new tickets.</span>
      </div>
    </Modal>
  )
}

/* ---------- tenant context edit / add (modal) ---------- */

interface CtxForm {
  tenant_id: string
  display_name: string
  region: string
  notes: string
  queue_hints: string  // "Queue: hint" per line
  priority_guidance: string  // one per line
  kb_hints: string  // "kb_id | topics comma | years comma | note" per line
}

const emptyCtxForm = (tenantId = ''): CtxForm => ({
  tenant_id: tenantId, display_name: '', region: 'IN', notes: '',
  queue_hints: '', priority_guidance: '', kb_hints: '',
})

function ctxToForm(tid: string, ctx?: TenantContext): CtxForm {
  if (!ctx) return emptyCtxForm(tid)
  return {
    tenant_id: tid,
    display_name: ctx.display_name ?? '',
    region: ctx.region ?? 'IN',
    notes: ctx.notes ?? '',
    queue_hints: Object.entries(ctx.queue_hints ?? {}).map(([k, v]) => `${k}: ${v}`).join('\n'),
    priority_guidance: (ctx.priority_guidance ?? []).join('\n'),
    kb_hints: (ctx.kb_hints ?? []).map((h) =>
      [h.kb_id, (h.topics ?? []).join(','), (h.years ?? []).join(','), h.note ?? ''].join(' | ')
    ).join('\n'),
  }
}

function formToPayload(f: CtxForm) {
  const queue_hints: Record<string, string> = {}
  for (const line of f.queue_hints.split('\n')) {
    const m = line.match(/^\s*([^:]+):\s*(.+)$/)
    if (m) queue_hints[m[1].trim()] = m[2].trim()
  }
  const kb_hints = f.kb_hints.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
    const [kb_id, topics, years, note] = l.split('|').map((s) => s.trim())
    return {
      kb_id: kb_id ?? '',
      topics: (topics ?? '').split(',').map((s) => s.trim()).filter(Boolean),
      years: (years ?? '').split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n)),
      ...(note ? { note } : {}),
    }
  }).filter((h) => h.kb_id)
  return {
    display_name: f.display_name || null,
    region: f.region,
    notes: f.notes || null,
    queue_hints,
    priority_guidance: f.priority_guidance.split('\n').map((s) => s.trim()).filter(Boolean),
    kb_hints,
  }
}

function TenantContextModal({ initial, tenantId, isNew, onSaved, onClose }: {
  initial: TenantContext | undefined
  tenantId: string
  isNew: boolean
  onSaved: (tid: string, ctx: any) => void
  onClose: () => void
}) {
  const [form, setForm] = useState<CtxForm>(() => ctxToForm(tenantId, initial))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const save = async () => {
    const tid = form.tenant_id.trim()
    if (!/^C-[0-9]{4,}$/.test(tid)) { setMsg('Tenant ID must look like C-1001.'); return }
    setBusy(true); setMsg('')
    try {
      const payload: any = formToPayload(form)
      // KB hints are hidden in the UI — preserve existing ones on edit.
      payload.kb_hints = isNew ? [] : (initial?.kb_hints ?? [])
      const saved = await api.upsertTenantContext(tid, payload)
      onSaved(tid, saved)
      onClose()
    } catch (e: any) {
      const d = e.message
      setMsg(`Error: ${typeof d === 'string' ? d : JSON.stringify(d)}`)
    }
    setBusy(false)
  }

  const set = (k: keyof CtxForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value })

  return (
    <Modal title={isNew ? 'Add tenant context' : `Edit tenant context — ${form.tenant_id}`} onClose={onClose} wide>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <label className="block">
          <span className={lblCls}>Tenant ID</span>
          <input value={form.tenant_id} onChange={set('tenant_id')} disabled={!isNew}
            placeholder="C-1003" className={`${inputCls} ${isNew ? '' : 'bg-ink-50 text-ink-500'}`} />
        </label>
        <label className="block">
          <span className={lblCls}>Display name</span>
          <input value={form.display_name} onChange={set('display_name')} placeholder="Acme Corp" className="w-full rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 text-[13px] outline-none focus:border-brand-500" />
        </label>
        <label className="block">
          <span className={lblCls}>Region</span>
          <select value={form.region} onChange={set('region')}
            className="w-full rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 text-[13px] outline-none focus:border-brand-500">
            <option value="IN">IN</option><option value="UAE">UAE</option>
          </select>
        </label>
      </div>
      <label className="mt-3 block">
        <span className={lblCls}>Notes (free text)</span>
        <textarea value={form.notes} onChange={set('notes')} rows={2}
          className="w-full resize-y rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 text-[13px] outline-none focus:border-brand-500" />
      </label>
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <label className="block">
          <span className={lblCls}>Queue hints — one per line: <code>Queue Name: hint text</code></span>
          <textarea value={form.queue_hints} onChange={set('queue_hints')} rows={5}
            placeholder={'Statutory-Compliance: PF, EPF, filings\nPayroll-Operations: salary credit, payslips'}
            className="w-full resize-y rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 font-mono text-[12px] outline-none focus:border-brand-500" />
        </label>
        <label className="block">
          <span className={lblCls}>Priority guidance — one per line</span>
          <textarea value={form.priority_guidance} onChange={set('priority_guidance')} rows={5}
            placeholder={'p1: payroll-wide outage or missed statutory deadline\np2: individual salary non-payment'}
            className="w-full resize-y rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 font-mono text-[12px] outline-none focus:border-brand-500" />
        </label>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button onClick={save} disabled={busy}
          className="rounded-lg bg-brand-500 px-4 py-2 text-[13px] font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
          {busy ? 'Saving…' : isNew ? 'Create tenant context' : 'Save context'}
        </button>
        {msg && <span className="text-[12px] text-error-600">{msg}</span>}
        <span className="ml-auto text-[11px] text-ink-400">Persists to routing_policy/tenant_context/&lt;id&gt;.yaml — live for the next LLM call.</span>
      </div>
    </Modal>
  )
}

/* ---------- page ---------- */

export function Rules({ names }: { tenants: string[]; names: Record<string, string> }) {
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [editingRule, setEditingRule] = useState<PolicyRule | null>(null)
  const [ctxTab, setCtxTab] = useState('')
  const [ctxModal, setCtxModal] = useState<{ tid: string; isNew: boolean } | null>(null)

  const load = useCallback(() => api.policy().then(setPolicy).catch(() => setPolicy(null)), [])
  useEffect(() => { load() }, [load])

  useEffect(() => {
    const ctx = policy?.tenant_contexts ?? {}
    const ids = Object.keys(ctx)
    if (!ctxTab && ids.length) setCtxTab(ids[0])
    else if (ctxTab && !ids.includes(ctxTab) && ids.length) setCtxTab(ids[0])
  }, [policy, ctxTab])

  const contexts = Object.entries(policy?.tenant_contexts ?? {}) as [string, TenantContext][]
  const selectedCtx = ctxTab ? contexts.find(([id]) => id === ctxTab)?.[1] : undefined

  const tbl = useTable(policy?.rules ?? [], {
    rule: (r) => r.id,
    scope: (r) => [r.tenant_id && `tenant ${names[r.tenant_id] ?? r.tenant_id}`, r.region && `region ${r.region}`].filter(Boolean).join(' ') || 'global',
    pattern: (r) => r.pattern ?? '',
    queue: (r) => r.queue_id ?? '',
    lane: (r) => r.lane ?? '',
    validity: (r) => `${r.valid_from ?? ''} ${r.valid_to ?? ''}`.trim() || 'always',
    status: (r) => r.status ?? '',
  })

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">Rules & tenant context</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">
            Deterministic routing rules — <b>authoritative</b>. The LLM only extracts signals; it never selects queues.
            {policy && <> Policy <code className="rounded bg-ink-100 px-1 text-[11px]">{policy.policy_version}</code>.</>}
          </p>
        </div>
        <button onClick={load} className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 bg-white text-ink-500 hover:bg-ink-100">
          <RefreshCw size={14} />
        </button>
      </div>

      <Card title="Routing rules" className="overflow-x-auto">
        {!policy && <Empty text="Loading policy…" />}
        {policy && (
          <table className="w-full min-w-[60rem] text-[13px]">
            <thead>
              <tr className="border-b border-ink-200 text-left text-ink-500">
                <Th t={tbl} k="rule" className="px-3 py-3">Rule</Th>
                <Th t={tbl} k="scope" className="px-3 py-3">Scope</Th>
                <Th t={tbl} k="pattern" className="px-3 py-3">Pattern</Th>
                <Th t={tbl} k="queue" className="px-3 py-3">Queue</Th>
                <Th t={tbl} k="lane" className="px-3 py-3">Lane</Th>
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
                    {r.reason_codes?.length ? <div className="mt-1 flex flex-wrap gap-1">{r.reason_codes.map((c) => <span key={c} className="rounded bg-ink-100 px-1 text-[10px] text-ink-500">{c}</span>)}</div> : null}
                  </td>
                  <td className="px-3 py-3 text-[12px] text-ink-600">
                    {r.tenant_id && <div>tenant <b>{names[r.tenant_id] ?? r.tenant_id}</b></div>}
                    {r.region && <div>region <b>{r.region}</b></div>}
                    {!r.tenant_id && !r.region && <span className="text-ink-400">global</span>}
                  </td>
                  <td className="max-w-[16rem] px-3 py-3"><code className="break-all font-mono text-[11px] text-ink-600">{r.pattern}</code></td>
                  <td className="px-3 py-3 font-medium text-ink-800">{r.queue_id}</td>
                  <td className="px-3 py-3"><Badge tone={r.lane}>{r.lane.replaceAll('_', ' ')}</Badge></td>
                  <td className="px-3 py-3 text-[11px] tabular-nums text-ink-500">
                    {r.valid_from || r.valid_to ? <>{r.valid_from ?? '…'} → {r.valid_to ?? '…'}</> : 'always'}
                  </td>
                  <td className="px-3 py-3"><Badge tone={r.status}>{r.status}</Badge></td>
                  <td className="px-3 py-3">
                    <button onClick={() => setEditingRule(r)}
                      className="rounded-lg border border-ink-200 p-1.5 text-ink-500 hover:bg-ink-100 hover:text-brand-500" title="Edit rule">
                      <Pencil size={13} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Tenant contexts — per-tenant tabs + detail pane */}
      <Card title={
        <span className="flex items-center justify-between gap-3">
          Tenant context
          <span className="text-[11px] font-normal text-ink-400">LLM guidance — not SLA policy</span>
        </span>
      }>
        <div className="flex flex-col gap-4 lg:flex-row">
          {/* left: tenant list */}
          <div className="flex shrink-0 flex-row gap-1 overflow-x-auto lg:w-52 lg:flex-col">
            {contexts.map(([tid, ctx]) => (
              <button key={tid} onClick={() => setCtxTab(tid)}
                className={`flex items-center justify-between rounded-lg px-3 py-2 text-left text-[13px] font-medium transition-colors ${
                  ctxTab === tid ? 'bg-brand-50 font-semibold text-brand-600' : 'text-ink-600 hover:bg-ink-100'}`}>
                <span className="truncate">{ctx.display_name ?? tid}</span>
                <span className="ml-2 font-mono text-[10px] text-ink-400">{tid}</span>
              </button>
            ))}
            <button onClick={() => setCtxModal({ tid: '', isNew: true })}
              className="flex items-center gap-1.5 rounded-lg border border-dashed border-ink-300 px-3 py-2 text-[12px] font-semibold text-ink-500 hover:border-brand-300 hover:text-brand-600">
              <Plus size={13} /> Add tenant
            </button>
          </div>

          {/* right: selected tenant context */}
          <div className="min-w-0 flex-1 rounded-xl border border-ink-100 bg-ink-50/50 p-4">
            {!selectedCtx || !ctxTab ? (
              <p className="py-6 text-center text-[13px] text-ink-400">Select a tenant or add one.</p>
            ) : (
              <>
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[15px] font-bold text-ink-900">{selectedCtx.display_name ?? ctxTab}</div>
                    <div className="text-[12px] text-ink-500">
                      <code className="rounded bg-ink-100 px-1 text-[11px]">{ctxTab}</code>
                      {selectedCtx.region ? <> · region <b>{selectedCtx.region}</b></> : null}
                    </div>
                  </div>
                  <button onClick={() => setCtxModal({ tid: ctxTab, isNew: false })}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-xs font-semibold text-ink-600 hover:bg-ink-100">
                    <Pencil size={12} /> Edit
                  </button>
                </div>
                {selectedCtx.notes && <p className="mb-3 rounded-lg bg-white px-3 py-2 text-[12px] leading-relaxed text-ink-600">{selectedCtx.notes}</p>}
                {selectedCtx.queue_hints && Object.keys(selectedCtx.queue_hints).length > 0 && (
                  <div className="mb-3">
                    <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wider text-ink-400">Queue hints</div>
                    <div className="flex flex-col gap-1.5">
                      {Object.entries(selectedCtx.queue_hints).map(([q, hint]) => (
                        <div key={q} className="rounded-lg bg-white px-3 py-2 text-[12px]">
                          <b className="text-ink-800">{q}</b>
                          <span className="text-ink-500"> — {hint}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {selectedCtx.priority_guidance?.length ? (
                  <div className="mb-3">
                    <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wider text-ink-400">Priority guidance</div>
                    <ul className="list-disc space-y-1 pl-5 text-[12px] text-ink-600">
                      {selectedCtx.priority_guidance.map((g, i) => <li key={i}>{g}</li>)}
                    </ul>
                  </div>
                ) : null}
              </>
            )}
          </div>
        </div>
      </Card>

      {editingRule && (
        <RuleEditModal rule={editingRule}
          onSaved={(updated) => setPolicy((p) => p && { ...p, rules: p.rules.map((x) => x.id === updated.id ? updated : x) })}
          onClose={() => setEditingRule(null)} />
      )}
      {ctxModal && (
        <TenantContextModal
          key={ctxModal.tid || 'new'}
          initial={ctxModal.isNew ? undefined : policy?.tenant_contexts?.[ctxModal.tid]}
          tenantId={ctxModal.tid}
          isNew={ctxModal.isNew}
          onSaved={(tid, saved) => {
            setPolicy((p) => p && { ...p, tenant_contexts: { ...(p.tenant_contexts ?? {}), [tid]: saved } })
            setCtxTab(tid)
          }}
          onClose={() => setCtxModal(null)} />
      )}
    </div>
  )
}
