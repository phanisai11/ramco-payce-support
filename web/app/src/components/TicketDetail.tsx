import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowLeft, Bot, Send, RefreshCw, Sparkles, User, Building2, GitBranch, Info, Paperclip } from 'lucide-react'
import { api, type TicketDetail as Detail } from '../api'
import { Badge, KV, StatusBadge } from './ui'
import { DecisionTrace } from './DecisionTrace'
import { useTicketDetail } from '../ticket'

const fmt = (iso?: string | null) =>
  iso ? iso.slice(0, 19).replace('T', ' ') : '—'

function FlowSteps({ d }: { d: Detail }) {
  const c = d.canonical ?? {}
  const ctime = c.time ?? {}
  const dec = d.decision ?? {}
  const steps = [
    { label: 'Received', ts: ctime.received_at ?? ctime.customer_message_at, state: 'done', note: 'customer message ingested' },
    { label: 'LLM analysis', ts: d.analysis?.created_at, state: d.analysis ? 'done' : 'pending', note: d.analysis?.payload?.status ?? 'not run' },
    { label: 'Classification', ts: dec.created_at, state: dec.lane ? 'done' : 'pending', note: `${(dec.lane ?? '—').replaceAll('_', ' ')} · ${dec.rule_id ?? 'no rule'}` },
    { label: 'Bot answer', ts: d.llm_answer?.payload?.composed_at ?? d.llm_answer?.created_at, state: d.llm_answer?.payload?.answer ? 'done' : d.llm_answer ? 'skipped' : 'pending', note: d.llm_answer?.payload?.verdict?.replaceAll('_', ' ') ?? 'not recorded' },
    { label: 'Resolution', ts: d.ticket.human_intervened_at ?? d.ticket.updated_at, state: ['resolved', 'closed'].includes(d.ticket.status) ? 'done' : 'pending', note: `${d.ticket.status}${d.ticket.human_intervened_at ? ' · human touched' : ''}` },
  ]
  const dot = (s: string) =>
    s === 'done' ? 'bg-brand-500 text-white' : s === 'skipped' ? 'bg-warning-500 text-white' : 'bg-ink-200 text-ink-400'
  return (
    <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
      <h3 className="mb-3 text-xs font-bold uppercase tracking-wider text-ink-500">Ticket flow</h3>
      <div className="flex flex-col gap-1">
        {steps.map((s, i) => (
          <div key={s.label} className="flex items-start gap-3">
            <div className="flex flex-col items-center">
              <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${dot(s.state)}`}>{i + 1}</div>
              {i < steps.length - 1 && <div className={`h-5 w-px ${s.state === 'done' ? 'bg-brand-200' : 'bg-ink-200'}`} />}
            </div>
            <div className="pb-2 pt-0.5">
              <div className="text-[13px] font-semibold text-ink-800">{s.label}</div>
              <div className="text-[11px] text-ink-500">{s.note}</div>
              <div className="font-mono text-[10px] text-ink-400">{fmt(s.ts)}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function TicketDetail({ tenant, ticketId, names, onChanged }: {
  tenant: string; ticketId: string
  names: Record<string, string>; onChanged?: () => void
}) {
  const { open } = useTicketDetail()
  const [d, setD] = useState<Detail | null>(null)
  const [err, setErr] = useState('')
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState('')
  const [draft, setDraft] = useState<any>(null)
  const [editingDraft, setEditingDraft] = useState(false)
  const [draftText, setDraftText] = useState('')
  const [attRows, setAttRows] = useState<any[]>([])
  const [viewer, setViewer] = useState<{ url: string; name: string; media: string; text?: string } | null>(null)
  const [viewerErr, setViewerErr] = useState('')
  const [view, setView] = useState<'conversation' | 'trace'>('conversation')

  const viewAttachment = async (a: any) => {
    setViewerErr('')
    try {
      const res = await fetch(api.attachmentContentUrl(tenant, ticketId, a.attachment_id))
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const media = res.headers.get('content-type') ?? a.detected_media_type ?? 'application/octet-stream'
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const isText = /text\/|json|csv|vtt|srt/.test(media)
      const text = isText ? await blob.text() : undefined
      setViewer({ url, name: a.filename ?? a.attachment_id, media, text })
    } catch (e: any) {
      setViewerErr(e.message)
    }
  }
  const closeViewer = () => {
    if (viewer) URL.revokeObjectURL(viewer.url)
    setViewer(null)
  }

  const load = useCallback(async () => {
    try {
      setD(await api.ticket(tenant, ticketId))
      api.ticketAttachments(tenant, ticketId)
        .then((r) => setAttRows(r.attachments ?? []))
        .catch(() => setAttRows([]))
    } catch (e: any) { setErr(e.message) }
  }, [tenant, ticketId])
  useEffect(() => { setD(null); setErr(''); setEditingDraft(false); setDraftText(''); load() }, [load])

  if (err) return <div className="rounded-xl2 border border-red-200 bg-red-50 p-4 text-sm text-error-600">{err}</div>
  if (!d) return <div className="rounded-xl2 border border-ink-200 bg-white p-8 text-center text-sm text-ink-500 shadow-card">Loading…</div>

  const c = d.canonical ?? {}
  const content = c.content ?? {}
  const facts = c.facts ?? {}
  const atts = c.attachments ?? []
  const dec = d.decision ?? {}
  const sla = d.sla ?? null
  const sig = d.analysis?.payload?.signals ?? {}
  const pri = (sig.priority ?? '').toUpperCase()
  const ans: any = d.llm_answer?.payload ?? null
  const closed = ['resolved', 'closed'].includes(d.ticket.status)

  const act = async (key: string, fn: () => Promise<any>) => {
    setBusy(key)
    try { await fn(); await load(); onChanged?.() } catch (e: any) { alert(e.message) }
    setBusy('')
  }
  const sendReply = (author: 'agent' | 'customer' | 'bot') => act('reply', async () => {
    if (!reply.trim()) return
    await api.reply(tenant, ticketId, author, reply.trim())
    setReply('')
  })
  const sendDraft = (as: 'bot' | 'agent', text?: string) => act('draft-send', async () => {
    const body = (text ?? ans?.answer ?? '').toString().trim()
    if (!body) return
    await api.reply(tenant, ticketId, as, body)
    if (as === 'bot' && !closed) {
      // Bot delivered the answer → resolve like an auto-answer ticket.
      await api.ticketStatus(tenant, ticketId, 'resolved')
    }
    setEditingDraft(false)
  })
  const draftBody = String(ans?.answer ?? '').trim()
  const draftLower = draftBody.toLowerCase()
  const draftSubstantive =
    draftBody.length >= 40 &&
    /[a-z0-9]/i.test(draftBody) &&
    !['do not provide', 'does not provide', 'no information available',
      'cannot find information', "don't have information"].some((p) => draftLower.includes(p))
  const botSent = !!ans?.answer && d.replies.some(
    (r) => r.author === 'bot' && r.body.trim() === draftBody,
  )
  // Once the ticket is resolved/closed the draft is moot — the answer was
  // delivered (bot auto-send, agent reply, or upgrade path).
  const showPendingDraft = draftSubstantive && !botSent && !closed


  const factRows: [string, any][] = [
    ['Category', facts.category],
    ['Payroll run ID', facts.run_id], ['Linked Jira', facts.jira_keys],
    ['KB articles', facts.kb_ids], ['Error codes', facts.error_codes],
    ['Refund refs', facts.refund_refs],
  ]

  return (
    <div className="flex flex-col gap-4">
      {/* header */}
      <div className="flex items-start justify-between gap-4 rounded-xl2 border border-ink-200 bg-white px-5 py-4 shadow-card">
        <div>
          <button onClick={() => open(null)} className="mb-1 inline-flex items-center gap-1 text-xs font-medium text-ink-500 hover:text-brand-500">
            <ArrowLeft size={13} /> Back
          </button>
          <div className="flex items-center gap-2 font-mono text-lg font-bold text-ink-900">
            {ticketId}
            {d.llm_cost && (
              <span className="rounded-md bg-emerald-50 px-1.5 py-0.5 font-sans text-[11px] font-semibold text-emerald-700"
                title={`${d.llm_cost.calls} LLM calls · ${d.llm_cost.input_tokens.toLocaleString()} in / ${d.llm_cost.output_tokens.toLocaleString()} out tokens`}>
                ${Number(d.llm_cost.cost_usd).toFixed(4)}
              </span>
            )}
          </div>
          <div className="text-xs text-ink-500">
            {names[tenant] ?? tenant} ({tenant}) · {c.ticket?.region ?? ''} · {d.ticket.source ?? 'unknown'} · v{d.ticket.canonical_version}
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <div className="mr-1 flex rounded-lg border border-ink-200 bg-ink-50 p-0.5">
            {(['conversation', 'trace'] as const).map((v) => (
              <button key={v} onClick={() => setView(v)}
                className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-semibold capitalize transition ${
                  view === v ? 'bg-white text-brand-600 shadow-sm' : 'text-ink-500 hover:text-ink-800'}`}>
                {v === 'trace' && <GitBranch size={11} />}{v === 'trace' ? 'Decision trace' : 'Conversation'}
              </button>
            ))}
          </div>
          <StatusBadge status={d.ticket.status} />
          {dec.lane && <Badge tone={dec.lane}>{dec.lane.replaceAll('_', ' ')}</Badge>}
          {pri && <Badge tone={pri.toLowerCase()}>{pri}</Badge>}
          {sig.language && sig.language !== 'en' && <Badge tone="p3">{sig.language}</Badge>}
        </div>
      </div>

      {view === 'trace' ? (
        <DecisionTrace d={d} attRows={attRows} />
      ) : (
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[16rem_minmax(0,1fr)_19rem]">
        {/* LEFT — requester + properties + sla */}
        <div className="flex flex-col gap-4">
          <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
            <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-ink-500">Requester</h3>
            <KV k="Tenant">{names[tenant] ?? tenant}</KV>
            <KV k="Channel">{d.ticket.source ?? 'unknown'}</KV>
            <KV k="Region">{c.ticket?.region ?? '—'}</KV>
            <KV k="Received">{fmt((c.time?.received_at ?? c.time?.customer_message_at) as string)}</KV>
            <KV k="Canonical">v{d.ticket.canonical_version}</KV>
          </div>
          <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
            <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-ink-500">Properties</h3>
            <KV k="Status"><StatusBadge status={d.ticket.status} /></KV>
            <KV k="Queue">{d.ticket.queue_id ?? '—'}</KV>
            <KV k="Routing rule"><code className="rounded bg-ink-100 px-1 text-[11px]">{dec.rule_id ?? d.ticket.rule_id ?? '—'}</code></KV>
            <KV k="Decided by">{dec.decided_by ?? '—'}</KV>
            <KV k="Auto-route">{dec.auto_route ? 'yes' : 'no'}</KV>
            <KV k="Last modified">{fmt(d.ticket.updated_at)}</KV>
            <KV k="Human intervention">{d.ticket.human_intervened_at ? fmt(d.ticket.human_intervened_at) : '—'}</KV>
            <KV k="LLM cost">
              {d.llm_cost
                ? `$${Number(d.llm_cost.cost_usd).toFixed(4)} · ${d.llm_cost.calls} calls`
                : '—'}
            </KV>
            {d.ticket.dedup_of && (
              <KV k="Duplicate of">
                <code className="rounded bg-violet-50 px-1 text-[11px] text-violet-700">{d.ticket.dedup_of}</code>
              </KV>
            )}
            <KV k="CSAT">
              {d.ticket.csat_score != null ? (
                <span className="inline-flex items-center gap-1">
                  <span className="text-amber-500">{'★'.repeat(d.ticket.csat_score)}<span className="text-ink-200">{'★'.repeat(5 - d.ticket.csat_score)}</span></span>
                  <span className="text-[11px] text-ink-400">{d.ticket.csat_score}/5</span>
                  {d.ticket.csat_comment && <span className="ml-1 text-[11px] italic text-ink-400">“{d.ticket.csat_comment}”</span>}
                </span>
              ) : 'not rated'}
            </KV>
            {dec.reason_codes?.length ? <KV k="Reason codes"><span className="break-words">{dec.reason_codes.join(', ')}</span></KV> : null}
          </div>
          <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
            <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-ink-500">Customer feedback</h3>
            {d.ticket.csat_score != null ? (
              <div className="rounded-lg bg-amber-50 px-3 py-2">
                <div className="text-amber-500">{'★'.repeat(d.ticket.csat_score)}<span className="text-amber-200">{'★'.repeat(5 - d.ticket.csat_score)}</span>
                  <span className="ml-1.5 text-[11px] font-semibold text-amber-700">{d.ticket.csat_score}/5</span>
                </div>
                {d.ticket.csat_comment
                  ? <div className="mt-1 text-[12px] italic text-ink-600">“{d.ticket.csat_comment}”</div>
                  : <div className="mt-1 text-[11px] text-ink-400">No comment left</div>}
                {d.ticket.csat_at && <div className="mt-0.5 text-[10px] text-ink-400">{fmt(d.ticket.csat_at)}</div>}
              </div>
            ) : (
              <div className="text-[12px] text-ink-400">No customer feedback yet — ratings arrive via the survey channel.</div>
            )}
          </div>
          {sla && (
            <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-ink-500">SLA</h3>
              {(() => {
                const hit = sla.breach as { sla_rule_id?: string; unanswered_hours?: number; currency?: string; penalty_amount?: string } | null;
                const next = sla.upcoming as typeof hit;
                const active = hit ?? next;
                return (
                  <>
                    <KV k="Rule">
                      <code className="rounded bg-ink-100 px-1 text-[11px]">
                        {active?.sla_rule_id ?? '—'}
                      </code>
                      {hit ? <span className="ml-1 text-[10px] font-semibold text-rose-600">BREACHED</span>
                        : next ? <span className="ml-1 text-[10px] font-semibold text-amber-600">NEXT TIER</span> : null}
                    </KV>
                    <KV k="Age / threshold">{sla.age_hours}h / {active?.unanswered_hours ?? '—'}h</KV>
                    <KV k="Penalty">{active ? `${active.currency} ${active.penalty_amount}` : '—'}</KV>
                    {sla.unanswered === false && (
                      <p className="mt-1 text-[11px] text-ink-400">
                        Ticket already answered — SLA clock stopped, no active penalty accrues.
                      </p>
                    )}
                    {!hit && !next && sla.unanswered ? <p className="mt-1 text-[11px] text-ink-400">No active SLA rule matches this ticket's status/tenant/region.</p> : null}
                  </>
                );
              })()}
            </div>
          )}
        </div>

        {/* CENTER — conversation (customer left · agent/bot right) */}
        <div className="flex min-w-0 flex-col gap-2.5">
          {/* original customer message — left */}
          <div className="flex justify-start">
            <div className="max-w-[88%] rounded-2xl rounded-bl-md border border-amber-200 bg-amber-50 p-3.5 shadow-sm">
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-amber-700">
                <User size={12} /> Customer
                <span className="font-normal text-ink-400">{d.ticket.source ?? 'unknown'} · {fmt((c.time?.customer_message_at ?? c.time?.received_at) as string)}</span>
              </div>
              <div className="font-semibold text-[13px] text-ink-900">{content.subject_masked ?? '—'}</div>
              <div className="mt-1 max-h-72 overflow-y-auto whitespace-pre-wrap text-[13px] leading-relaxed text-ink-800">{content.body_masked ?? '—'}</div>
              {attRows.length > 0 && (
                <div className="mt-2.5 border-t border-amber-200/70 pt-2">
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-amber-600">Customer attached</div>
                  <div className="flex flex-wrap gap-1.5">
                    {attRows.map((a: any) => (
                      <button key={a.attachment_id} onClick={() => viewAttachment(a)}
                        className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-2 py-1 text-[11px] font-medium text-amber-800 hover:border-amber-500 hover:bg-amber-50">
                        <Paperclip size={11} className="shrink-0" />
                        <span className="truncate">{a.filename ?? a.attachment_id?.slice(0, 8)}</span>
                        {a.extracted_summary && <span className="hidden text-ink-400 sm:inline">· {String(a.extracted_summary).slice(0, 40)}</span>}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* replies — customer left, agent/bot right, system centered */}
          {d.replies.map((r) => {
            if (r.author === 'system') {
              return (
                <div key={r.reply_id} className="flex justify-center">
                  <div className="inline-flex max-w-[90%] items-center gap-1.5 rounded-full border border-ink-200 bg-ink-50 px-3 py-1 text-[11px] text-ink-500">
                    <Info size={11} /> {r.body}
                    <span className="text-ink-300">· {fmt(r.created_at)}</span>
                  </div>
                </div>
              )
            }
            const isCustomer = r.author === 'customer'
            const isAgent = r.author === 'agent'
            return (
              <div key={r.reply_id} className={`flex ${isCustomer ? 'justify-start' : 'justify-end'}`}>
                <div className={`max-w-[88%] rounded-2xl p-3.5 shadow-sm ${
                  isCustomer ? 'rounded-bl-md border border-amber-200 bg-amber-50'
                  : isAgent ? 'rounded-br-md border border-success-200 bg-success-50/60'
                  : 'rounded-br-md border border-brand-200 bg-brand-25/60'}`}>
                  <div className={`mb-1 flex items-center gap-1.5 text-[11px] font-semibold ${
                    isCustomer ? 'text-amber-700' : isAgent ? 'text-success-700' : 'text-brand-600'}`}>
                    {isCustomer ? <User size={12} /> : isAgent ? <Building2 size={12} /> : <Bot size={12} />}
                    {isCustomer ? 'Customer' : isAgent ? 'Agent' : 'Bot'}
                    <span className="font-normal text-ink-400">{fmt(r.created_at)}</span>
                  </div>
                  <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-800">{r.body}</div>
                </div>
              </div>
            )
          })}

          {/* pending bot draft — right, awaiting human review */}
          {showPendingDraft && (
            <div className="flex justify-end">
              <div className="max-w-[88%] rounded-2xl rounded-br-md border-2 border-dashed border-brand-300 bg-brand-25/50 p-3.5">
                <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[11px] font-semibold text-brand-700">
                  <Bot size={12} /> Bot draft — awaiting review
                  {ans?.verdict && <Badge tone={ans.verdict === 'auto_answer' ? 'approved' : ans.verdict === 'draft_for_agent' ? 'in_progress' : 'expired'}>{ans.verdict.replaceAll('_', ' ')}</Badge>}
                  <span className="font-normal text-ink-400">{ans?.composed_at ? fmt(ans.composed_at) : 'post-routing'}</span>
                </div>
                {editingDraft ? (
                  <>
                    <textarea value={draftText} onChange={(e) => setDraftText(e.target.value)} rows={6}
                      className="mt-1 w-full resize-y rounded-lg border border-amber-300 bg-white px-3 py-2 text-[13px] leading-relaxed text-ink-800 outline-none focus:border-amber-500 focus:ring-2 focus:ring-amber-500/15" />
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <button onClick={() => sendDraft('agent', draftText)} disabled={!!busy || !draftText.trim()}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-amber-600 disabled:opacity-50">
                        <Building2 size={12} /> Send as human{closed ? '' : ' (awaits customer)'}
                      </button>
                      <button onClick={() => { setEditingDraft(false); setDraftText('') }} disabled={!!busy}
                        className="rounded-lg border border-ink-300 bg-white px-3 py-1.5 text-[11px] font-semibold text-ink-600 hover:bg-ink-100 disabled:opacity-50">
                        Cancel
                      </button>
                      <span className="text-[10px] font-medium text-amber-600">edited by human — can only be sent as agent</span>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="whitespace-pre-wrap rounded-lg border border-ink-100 bg-white p-3 text-[13px] leading-relaxed text-ink-800">{ans.answer}</div>
                    {ans.citations?.length ? (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {ans.citations.map((x: string) => <code key={x} className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">{x}</code>)}
                      </div>
                    ) : null}
                    <div className="mt-2.5 flex flex-wrap gap-2">
                      <button onClick={() => sendDraft('bot')} disabled={!!busy}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-brand-500 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
                        <Bot size={12} /> Send as bot{closed ? '' : ' (resolves)'}
                      </button>
                      <button onClick={() => sendDraft('agent')} disabled={!!busy}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-success-300 bg-success-50 px-3 py-1.5 text-[11px] font-semibold text-success-700 hover:bg-success-100 disabled:opacity-50">
                        <Building2 size={12} /> Send as human{closed ? '' : ' (awaits customer)'}
                      </button>
                      <button onClick={() => { setDraftText(String(ans.answer ?? '')); setEditingDraft(true) }} disabled={!!busy}
                        className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-[11px] font-semibold text-amber-700 hover:bg-amber-100 disabled:opacity-50">
                        Edit
                      </button>
                      <span className="self-center text-[10px] text-ink-400">not sent to customer yet</span>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {/* composer */}
          <div className="rounded-xl2 border border-dashed border-brand-200 bg-brand-25/30 p-4">
            <textarea value={reply} onChange={(e) => setReply(e.target.value)} rows={3}
              placeholder="Type a reply…"
              className="w-full resize-y rounded-lg border border-ink-300 bg-white px-3 py-2 text-[13px] outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15" />
            <div className="mt-2.5 flex flex-wrap gap-2">
              <button onClick={() => sendReply('agent')} disabled={!!busy}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-500 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
                <Send size={13} /> Send as agent{closed ? '' : ' (awaits customer)'}
              </button>
              <button onClick={() => sendReply('customer')} disabled={!!busy}
                className="rounded-lg border border-brand-200 bg-white px-3.5 py-1.5 text-xs font-semibold text-brand-500 hover:bg-brand-50 disabled:opacity-50">
                Simulate customer reply
              </button>
              {closed ? (
                <button onClick={() => act('reopen', () => api.reopen(tenant, ticketId))} disabled={!!busy}
                  className="rounded-lg border border-ink-300 bg-white px-3.5 py-1.5 text-xs font-semibold text-ink-600 hover:bg-ink-100 disabled:opacity-50">
                  Reopen ticket
                </button>
              ) : (
                <>
                  <button onClick={() => act('progress', () => api.ticketStatus(tenant, ticketId, 'in_progress'))} disabled={!!busy}
                    className="rounded-lg border border-amber-300 bg-amber-50 px-3.5 py-1.5 text-xs font-semibold text-amber-700 hover:bg-amber-100 disabled:opacity-50">
                    Mark in progress
                  </button>
                  <button onClick={() => act('pending', () => api.ticketStatus(tenant, ticketId, 'pending_customer'))} disabled={!!busy}
                    className="rounded-lg border border-sky-300 bg-sky-50 px-3.5 py-1.5 text-xs font-semibold text-sky-700 hover:bg-sky-100 disabled:opacity-50">
                    Awaiting customer
                  </button>
                  <button onClick={() => act('resolve', () => api.ticketStatus(tenant, ticketId, 'resolved'))} disabled={!!busy}
                    className="rounded-lg border border-success-500/40 bg-success-50 px-3.5 py-1.5 text-xs font-semibold text-success-700 hover:bg-success-100 disabled:opacity-50">
                    Mark resolved
                  </button>
                </>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT — flow + AI + facts */}
        <div className="flex flex-col gap-4">
          <FlowSteps d={d} />
          <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
            <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-ink-500">LLM analysis</h3>
            {d.analysis ? (
              <>
                <KV k="Status">{d.analysis.payload?.status ?? '—'}</KV>
                <KV k="Model">{d.analysis.model_id ?? d.analysis.payload?.model_id ?? '—'}</KV>
                <KV k="Saved at">{fmt(d.analysis.created_at)}</KV>
                {d.analysis.payload?.declared_priority ? <KV k="Declared priority">{d.analysis.payload.declared_priority}</KV> : null}
                {sig.priority ? <KV k="AI priority"><Badge tone={String(sig.priority).toLowerCase()}>{String(sig.priority).toUpperCase()}</Badge>{sig.p1 ? <> <Badge tone="p1">P1 flag</Badge></> : null}</KV> : null}
                {sig.summary ? <KV k="AI summary"><span className="break-words text-left">{sig.summary}</span></KV> : null}
                {d.analysis.payload?.reason ? <KV k="Reason">{d.analysis.payload.reason}</KV> : null}
              </>
            ) : <p className="text-[13px] text-ink-500">No analysis saved yet.</p>}
            <button onClick={() => act('analyze', () => api.analyze(tenant, ticketId))} disabled={!!busy}
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-brand-200 bg-white px-3 py-1.5 text-xs font-semibold text-brand-500 hover:bg-brand-50 disabled:opacity-50">
              <RefreshCw size={12} /> Run LLM analysis
            </button>
            <button onClick={() => act('queue', () => api.suggestQueue(tenant, ticketId))} disabled={!!busy}
              className="ml-2 mt-3 inline-flex items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-semibold text-violet-600 hover:bg-violet-100 disabled:opacity-50">
              <Sparkles size={12} /> AI queue suggestion
            </button>
          </div>
          <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
            <h3 className="mb-1 text-xs font-bold uppercase tracking-wider text-ink-500">Bot draft (RAG)</h3>
            <p className="mb-2 text-[11px] text-ink-500">Grounded answer attempt against the tenant-scoped KB, gated by answerability policy.</p>
            <button onClick={async () => { setBusy('draft'); try { setDraft(await api.draft(tenant, ticketId)) } catch (e: any) { alert(e.message) } setBusy('') }} disabled={!!busy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-brand-200 bg-white px-3 py-1.5 text-xs font-semibold text-brand-500 hover:bg-brand-50 disabled:opacity-50">
              <Sparkles size={12} /> Generate bot draft
            </button>
            {draft && (
              <div className="mt-3 space-y-1.5 border-t border-ink-100 pt-3 text-[12px]">
                <KV k="Verdict"><Badge tone={draft.verdict === 'auto_answer' ? 'approved' : draft.verdict === 'draft_for_agent' ? 'in_progress' : 'expired'}>{draft.verdict?.replaceAll('_', ' ')}</Badge></KV>
                <KV k="Reasons"><span className="break-words">{(draft.verdict_reasons ?? []).join(', ')}</span></KV>
                <KV k="RAG">{draft.rag_status} ({draft.rag_reason_code})</KV>
                <KV k="Provider">{draft.llm_provider}{draft.cache_hit ? ' · cache' : ''} · {draft.input_tokens}/{draft.output_tokens} tok</KV>
                {draft.citations?.length ? <KV k="Citations"><span className="flex flex-wrap justify-end gap-1">{draft.citations.map((x: string) => <code key={x} className="rounded bg-emerald-50 px-1 text-[10px] text-emerald-700">{x}</code>)}</span></KV> : null}
                {draft.answer && <div className="mt-2 whitespace-pre-wrap rounded-lg bg-ink-50 p-2.5 text-[12px] leading-relaxed text-ink-800">{draft.answer}</div>}
              </div>
            )}
          </div>
          <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
            <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-ink-500">Extracted facts</h3>
            {factRows.some(([, v]) => v != null && v !== '' && !(Array.isArray(v) && !v.length))
              ? factRows.filter(([, v]) => v != null && v !== '' && !(Array.isArray(v) && !v.length)).map(([k, v]) => (
                  <KV key={k} k={k}><span className="break-words">{Array.isArray(v) ? v.join(', ') : String(v)}</span></KV>
                ))
              : <p className="text-[13px] text-ink-500">No structured facts extracted.</p>}
          </div>
          <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
            <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-ink-500">Attachments ({attRows.length || atts.length})</h3>
            {attRows.length ? attRows.map((a: any) => (
              <div key={a.attachment_id} className="mb-3 rounded-lg border border-ink-100 bg-ink-50/50 p-2.5 last:mb-0">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-[12px] font-semibold text-ink-800" title={a.filename ?? a.attachment_id}>
                      {a.filename ?? a.attachment_id?.slice(0, 8)}
                    </div>
                    <div className="text-[10px] text-ink-400">
                      {a.detected_media_type ?? 'unknown'} · {Math.round((a.size_bytes ?? 0) / 1024)} KB
                      {a.processor ? ` · ${a.processor}` : ''}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {a.has_content && (
                      <button onClick={() => viewAttachment(a)}
                         className="rounded-md border border-ink-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-ink-600 hover:border-brand-300 hover:text-brand-600">
                        View
                      </button>
                    )}
                  </div>
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  <Badge tone={a.scan_status === 'clean' ? 'approved' : a.scan_status === 'blocked' ? 'expired' : 'scheduled'}>{a.scan_status}</Badge>
                  {a.pii_class && a.pii_class !== 'none' && <Badge tone="p2">PII: {a.pii_class}</Badge>}
                </div>
                {a.extracted_summary && <p className="mt-1 text-[11px] text-ink-500">{a.extracted_summary}</p>}
                {a.extracted_text && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-[11px] font-medium text-brand-600">extracted text</summary>
                    <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[10px] text-ink-600">{a.extracted_text.slice(0, 1500)}</pre>
                  </details>
                )}
              </div>
            )) : atts.length
              ? atts.map((a: any, i: number) => <KV key={i} k={a.filename ?? a.attachment_id}>{a.scan_status} / {a.pii_class} / {a.processing_status}</KV>)
              : <p className="text-[13px] text-ink-500">none</p>}
          </div>
          <div className="rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
            <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-ink-500">Provenance</h3>
            {Object.keys(c.provenance ?? {}).length
              ? Object.entries(c.provenance).map(([k, v]) => <KV key={k} k={k.replaceAll('_', ' ')}><code className="rounded bg-ink-100 px-1 text-[10px]">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</code></KV>)
              : <p className="text-[13px] text-ink-500">No provenance recorded.</p>}
          </div>
        </div>
      </div>
      )}

      {/* attachment viewer — portaled to body so `fixed` escapes the
          blurred detail-overlay containing block */}
      {viewerErr && <div className="rounded-xl2 border border-red-200 bg-red-50 p-3 text-sm text-error-600">{viewerErr}</div>}
      {viewer && createPortal(
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-ink-900/60 p-6" onClick={closeViewer}>
          <div className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl2 border border-ink-200 bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-ink-100 px-4 py-2.5">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-ink-800">{viewer.name}</div>
                <div className="text-[11px] text-ink-400">{viewer.media}</div>
              </div>
              <div className="flex items-center gap-2">
                <a href={viewer.url} download={viewer.name}
                   className="rounded-md border border-ink-200 px-2 py-1 text-[11px] font-medium text-ink-600 hover:border-brand-300 hover:text-brand-600">
                  Download
                </a>
                <button onClick={closeViewer} className="rounded-md px-2 py-1 text-sm text-ink-500 hover:bg-ink-100">✕</button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto bg-ink-50 p-4">
              {viewer.text !== undefined ? (
                <pre className="whitespace-pre-wrap rounded-lg bg-white p-4 font-mono text-[12px] leading-relaxed text-ink-700">{viewer.text}</pre>
              ) : /^image\//.test(viewer.media) ? (
                <img src={viewer.url} alt={viewer.name} className="mx-auto max-h-[70vh] rounded-lg border border-ink-200" />
              ) : viewer.media.includes('pdf') ? (
                <iframe src={viewer.url} title={viewer.name} className="h-[70vh] w-full rounded-lg border border-ink-200 bg-white" />
              ) : viewer.media.startsWith('audio/') ? (
                <div className="flex h-40 items-center justify-center"><audio src={viewer.url} controls className="w-96" /></div>
              ) : (
                <div className="flex h-40 flex-col items-center justify-center gap-2 text-ink-500">
                  <p className="text-sm">No inline preview for {viewer.media}</p>
                  <a href={viewer.url} download={viewer.name} className="text-sm font-medium text-brand-600 hover:underline">Download file</a>
                </div>
              )}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
