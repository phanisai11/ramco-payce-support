import { useState } from 'react'
import { Send } from 'lucide-react'
import { api, b64 } from '../api'
import { useTicketDetail } from '../ticket'

export function Intake({ tenants, names, onCreated }: {
  tenants: string[]; names: Record<string, string>; onCreated?: () => void
}) {
  const { open } = useTicketDetail()
  const [tenant, setTenant] = useState(tenants[0] ?? '')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [err, setErr] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!tenant || !subject.trim() || !body.trim()) { setErr('Tenant, subject and body are required.'); return }
    setBusy(true); setErr(''); setResult(null)
    // Ticket ID, status and age are system/backend-owned — never taken
    // from the UI. ID format: PN-<tenant suffix>-<random>.
    const tid = `PN-${tenant.replace(/\D/g, '') || 'X'}-${Date.now().toString(36).toUpperCase()}${Math.floor(Math.random() * 36 ** 3).toString(36).toUpperCase()}`
    const raw = JSON.stringify({ ticket_id: tid, case_id: `CASE-${tid}`, subject: subject.trim(), body: body.trim() })
    const now = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
    try {
      const res = await api.process({
        principal_id: 'dashboard', channel_account_id: 'widget-in', ingress_region: 'IN',
        authenticated_tenant_id: tenant, verified_contact_id: tenant === 'C-1001' ? 'contact-a' : null,
        source: 'widget', source_event_id: `ui-${tid}-${Date.now()}`,
        received_at: now, source_occurred_at: now, payload_schema: 'widget/v1',
        raw_body_base64: b64(raw), parsed_payload: JSON.parse(raw), attachments: [],
        p1: false, priority: null,
        ticket_status: 'open', ticket_age_hours: null,
      })
      setResult(res)
      onCreated?.()
      open({ tenant, ticket: res.ticket_id })
    } catch (e2: any) { setErr(e2.message) }
    setBusy(false)
  }

  const input = 'w-full rounded-lg border border-ink-300 bg-white px-3 py-2 text-[13px] text-ink-800 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15'
  const lbl = 'mb-1 block text-xs font-semibold text-ink-600'

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <div>
        <h2 className="text-xl font-bold tracking-tight text-ink-900">Submit a ticket</h2>
        <p className="mt-0.5 text-[13px] text-ink-500">
          Posts a real event through <code className="rounded bg-ink-100 px-1 text-[11px]">POST /v1/pipeline/process</code>:
          intake → masked canonical event → deterministic routing → persisted LLM analysis + bot answer.
        </p>
      </div>

      <form onSubmit={submit} className="rounded-xl2 border border-ink-200 bg-white p-6 shadow-card">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className={lbl}>Tenant</label>
            <select value={tenant} onChange={(e) => setTenant(e.target.value)} className={input} required>
              {tenants.map((t) => <option key={t} value={t}>{names[t] ?? t} ({t})</option>)}
            </select>
          </div>
          <div>
            <label className={lbl}>Status</label>
            <input value="open" disabled className={`${input} bg-ink-50 text-ink-500`} />
          </div>
          <div className="sm:col-span-2">
            <label className={lbl}>Subject</label>
            <input value={subject} onChange={(e) => setSubject(e.target.value)} required maxLength={200}
              placeholder="e.g. Payroll run failed — how do I retry?" className={input} />
          </div>
          <div className="sm:col-span-2">
            <label className={lbl}>Message body (customer-style)</label>
            <textarea value={body} onChange={(e) => setBody(e.target.value)} required rows={7}
              placeholder="Hi team, our payroll run RUN-… failed overnight and 200 employees have no payslips. How do I retry it?"
              className={`${input} resize-y leading-relaxed`} />
          </div>
        </div>
        {err && <p className="mt-3 text-[13px] text-error-600">{err}</p>}
        {result && (
          <p className="mt-3 rounded-lg bg-success-50 px-3 py-2 text-[13px] text-success-700">
            Ticket <b className="font-mono">{result.ticket_id}</b> · lane <b>{(result.lane ?? '').replaceAll('_', ' ')}</b> · rule <code>{result.rule_id ?? '—'}</code> · auto_route={String(result.auto_route)} — detail opened below.
          </p>
        )}
        <div className="mt-5 flex items-center gap-3">
          <button type="submit" disabled={busy}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-500 px-4 py-2 text-[13px] font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
            <Send size={14} /> {busy ? 'Processing…' : 'Submit & trace flow'}
          </button>
        </div>
      </form>
    </div>
  )
}
