import type { TicketDetail as Detail } from '../api'
import { Badge, KV } from './ui'

const fmt = (iso?: string | null) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

function Stage({ index, title, status, children }: {
  index: number; title: string; status?: 'done' | 'skipped' | 'pending'
  children: React.ReactNode
}) {
  const chip =
    status === 'done' ? 'bg-brand-500 text-white'
    : status === 'skipped' ? 'bg-ink-300 text-white'
    : 'bg-ink-100 text-ink-400'
  return (
    <div className="relative flex gap-4 pb-6 last:pb-0">
      <div className="flex flex-col items-center">
        <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${chip}`}>{index}</div>
        <div className="mt-1 w-px flex-1 bg-ink-200 last:hidden" />
      </div>
      <div className="min-w-0 flex-1 rounded-xl2 border border-ink-200 bg-white p-4 shadow-card">
        <h4 className="mb-2 text-[13px] font-bold text-ink-900">{title}</h4>
        {children}
      </div>
    </div>
  )
}

function JsonBlock({ data }: { data: any }) {
  if (data == null) return <p className="text-[12px] text-ink-400">—</p>
  return (
    <pre className="max-h-56 overflow-auto rounded-lg bg-ink-50 p-2.5 font-mono text-[10.5px] leading-relaxed text-ink-700">
      {JSON.stringify(data, null, 1)}
    </pre>
  )
}

/** Decision Explorer — full lineage of one ticket: intake → canonical →
 * signals → deterministic decision → LLM assists → SLA → answer → files. */
export function DecisionTrace({ d, attRows }: { d: Detail; attRows: any[] }) {
  const c = d.canonical ?? {}
  const content = c.content ?? {}
  const ctime = c.time ?? {}
  const facts = c.facts ?? {}
  const dec = d.decision ?? {}
  const sig = d.analysis?.payload?.signals ?? {}
  const llmQueue = d.llm_queue?.payload ?? null
  const ans: any = d.llm_answer?.payload ?? null
  const sla = d.sla ?? null

  const slaHit = sla?.breach as any
  const slaNext = sla?.upcoming as any
  const slaActive = slaHit ?? slaNext

  return (
    <div className="rounded-xl2 border border-ink-200 bg-ink-50/60 p-5">
      <div className="mb-4">
        <h3 className="text-sm font-bold text-ink-900">Decision trace</h3>
        <p className="text-[12px] text-ink-500">
          Every stage that touched this ticket, in order. Deterministic stages are authoritative;
          LLM stages are signals/assists only.
        </p>
      </div>

      {/* 1 — intake */}
      <Stage index={1} title="Intake & canonical event" status="done">
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
          <KV k="Source">{d.ticket.source ?? '—'}</KV>
          <KV k="Region">{c.ticket?.region ?? '—'}</KV>
          <KV k="Canonical">v{d.ticket.canonical_version}</KV>
          <KV k="Received">{fmt(ctime.received_at)}</KV>
          <KV k="Message at">{fmt(ctime.customer_message_at)}</KV>
          <KV k="t_as_of">{fmt(ctime.t_as_of)}</KV>
          <KV k="Payroll period">{ctime.payroll_period ?? '—'}</KV>
          <KV k="Payroll week">{ctime.payroll_week ? 'yes (25th→3rd)' : 'no'}</KV>
          <KV k="Ticket id hint">{facts.ticket_id ?? content.ticket_id ?? '—'}</KV>
        </div>
        <div className="mt-3 rounded-lg border border-ink-100 bg-ink-50 p-3">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ink-400">Masked content</div>
          <div className="text-[13px] font-semibold text-ink-900">{content.subject_masked ?? '—'}</div>
          <div className="mt-1 max-h-28 overflow-y-auto whitespace-pre-wrap text-[12px] leading-relaxed text-ink-600">{content.body_masked ?? '—'}</div>
        </div>
      </Stage>

      {/* 2 — LLM signals */}
      <Stage index={2} title="LLM signal extraction" status={d.analysis ? 'done' : 'pending'}>
        {d.analysis ? (
          <>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-4">
              <KV k="Extractor"><Badge tone={d.analysis.payload?.extractor === 'llm' ? 'in_progress' : 'scheduled'}>{d.analysis.payload?.extractor ?? '—'}</Badge></KV>
              <KV k="Model">{d.analysis.model_id ?? d.analysis.payload?.model_id ?? '—'}</KV>
              <KV k="Tokens">{d.analysis.payload?.input_tokens ?? 0} / {d.analysis.payload?.output_tokens ?? 0}</KV>
              <KV k="Status">{d.analysis.payload?.status ?? '—'}</KV>
              <KV k="Priority"><Badge tone={String(sig.priority ?? 'p3').toLowerCase()}>{String(sig.priority ?? '—').toUpperCase()}</Badge></KV>
              <KV k="Language">{sig.language ?? '—'}</KV>
              <KV k="Intent">{sig.intent ?? '—'}</KV>
              <KV k="Confidence">{sig.confidence ?? '—'}</KV>
            </div>
            {sig.summary && <KV k="Summary"><span className="break-words text-left">{sig.summary}</span></KV>}
            {sig.entities?.length ? <KV k="Entities"><span className="break-words">{sig.entities.join(', ')}</span></KV> : null}
            {sig.rag_hints?.length ? <KV k="KB hints"><span className="break-words">{sig.rag_hints.join(', ')}</span></KV> : null}
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] font-semibold text-ink-400 hover:text-brand-500">Raw payload</summary>
              <div className="mt-1"><JsonBlock data={d.analysis.payload} /></div>
            </details>
          </>
        ) : <p className="text-[12px] text-ink-400">No llm_signals analysis recorded.</p>}
      </Stage>

      {/* 3 — deterministic routing */}
      <Stage index={3} title="Deterministic routing decision" status={dec.rule_id ? 'done' : 'pending'}>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-4">
          <KV k="Rule"><code className="rounded bg-ink-100 px-1 text-[11px]">{dec.rule_id ?? '—'}</code></KV>
          <KV k="Lane"><Badge tone={dec.lane}>{(dec.lane ?? '—').replaceAll('_', ' ')}</Badge></KV>
          <KV k="Queue">{dec.queue_id ?? '—'}</KV>
          <KV k="Decided by">{dec.decided_by ?? '—'}</KV>
          <KV k="Auto-route">{dec.auto_route ? 'yes' : 'no — abstained to human'}</KV>
          <KV k="Policy">{dec.policy_version ?? '—'}</KV>
          <KV k="Canonical v">{dec.canonical_version ?? '—'}</KV>
          <KV k="Confidence">{dec.confidence ?? '—'}</KV>
        </div>
        {dec.reason_codes?.length ? <KV k="Reason codes"><span className="break-words">{dec.reason_codes.join(', ')}</span></KV> : null}
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] font-semibold text-ink-400 hover:text-brand-500">Decision record</summary>
          <div className="mt-1"><JsonBlock data={dec} /></div>
        </details>
      </Stage>

      {/* 4 — LLM queue assist */}
      <Stage index={4} title="LLM queue suggestion (assist only)" status={llmQueue ? 'done' : 'skipped'}>
        {llmQueue ? (
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-4">
            <KV k="Suggested"><code className="rounded bg-violet-50 px-1 text-[11px] text-violet-700">{llmQueue.queue_id}</code></KV>
            <KV k="Confidence">{llmQueue.confidence ?? '—'}</KV>
            <KV k="Applied">{llmQueue.applied ? 'yes' : 'no — decision unchanged'}</KV>
            <KV k="New queue?">{llmQueue.is_new_queue ? 'yes (proposed)' : 'no'}</KV>
            {llmQueue.rationale && <KV k="Rationale"><span className="break-words text-left">{llmQueue.rationale}</span></KV>}
          </div>
        ) : <p className="text-[12px] text-ink-400">Not invoked — rule base classified the ticket.</p>}
      </Stage>

      {/* 5 — SLA */}
      <Stage index={5} title="SLA evaluation" status={slaActive ? 'done' : 'skipped'}>
        {sla ? (
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-4">
            <KV k="Rule"><code className="rounded bg-ink-100 px-1 text-[11px]">{slaActive?.sla_rule_id ?? '—'}</code></KV>
            <KV k="Tier">{slaHit ? <Badge tone="expired">BREACHED</Badge> : slaNext ? <Badge tone="in_progress">NEXT</Badge> : '—'}</KV>
            <KV k="Age / threshold">{sla.age_hours}h / {slaActive?.unanswered_hours ?? '—'}h</KV>
            <KV k="Penalty">{slaActive ? `${slaActive.currency} ${slaActive.penalty_amount}` : '—'}</KV>
          </div>
        ) : <p className="text-[12px] text-ink-400">No SLA evaluation.</p>}
      </Stage>

      {/* 6 — answer */}
      <Stage index={6} title="Grounded answer (RAG + gate)" status={ans?.answer ? 'done' : ans ? 'skipped' : 'pending'}>
        {ans ? (
          <>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-4">
              <KV k="Verdict"><Badge tone={ans.verdict === 'auto_answer' ? 'approved' : ans.verdict === 'draft_for_agent' ? 'in_progress' : 'expired'}>{(ans.verdict ?? '—').replaceAll('_', ' ')}</Badge></KV>
              <KV k="RAG">{ans.status} · {ans.reason_code}</KV>
              <KV k="Citations">{ans.citations?.length ?? 0}</KV>
              <KV k="Tokens">{ans.input_tokens ?? 0} / {ans.output_tokens ?? 0}</KV>
            </div>
            {ans.verdict_reasons?.length ? <KV k="Gate reasons"><span className="break-words">{ans.verdict_reasons.join(', ')}</span></KV> : null}
            {ans.citations?.length ? (
              <div className="mt-1.5 flex flex-wrap gap-1">{ans.citations.map((x: string) => <code key={x} className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">{x}</code>)}</div>
            ) : null}
            {ans.answer && <div className="mt-2 whitespace-pre-wrap rounded-lg border border-ink-100 bg-ink-50 p-3 text-[12px] leading-relaxed text-ink-800">{ans.answer}</div>}
          </>
        ) : <p className="text-[12px] text-ink-400">No answer attempt recorded.</p>}
      </Stage>

      {/* 7 — attachments */}
      <Stage index={7} title={`Attachments (${attRows.length || (c.attachments ?? []).length})`} status={attRows.length ? 'done' : 'skipped'}>
        {attRows.length ? (
          <div className="flex flex-col gap-1.5">
            {attRows.map((a) => (
              <div key={a.attachment_id} className="flex flex-wrap items-center gap-2 rounded-lg border border-ink-100 bg-ink-50 px-3 py-2 text-[12px]">
                <span className="font-mono font-semibold text-ink-800">{a.filename}</span>
                <Badge tone="scheduled">{a.processor ?? 'unprocessed'}</Badge>
                {a.pii_class && a.pii_class !== 'none' ? <Badge tone="p2">PII: {a.pii_class}</Badge> : null}
                {a.extracted_summary && <span className="text-[11px] text-ink-500">{a.extracted_summary}</span>}
              </div>
            ))}
          </div>
        ) : <p className="text-[12px] text-ink-400">No attachments on this ticket.</p>}
      </Stage>
    </div>
  )
}
