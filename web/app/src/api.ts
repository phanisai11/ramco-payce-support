// Typed fetch helpers for the Payce support API (proxied to :8000 in dev).

export interface Ticket {
  tenant_id: string
  ticket_id: string
  status: string
  queue_id: string | null
  rule_id: string | null
  lane: string | null
  auto_route: boolean | null
  decided_by: string | null
  canonical_version: number
  event_id: string
  updated_at: string
  human_intervened_at: string | null
  source: string | null
  priority: string | null
  dedup_of?: string | null
  csat_score?: number | null
  csat_at?: string | null
  csat_comment?: string | null
}

export interface Summary {
  total: number
  by_status: Record<string, number>
  by_queue: Record<string, number>
  by_channel: Record<string, number>
  auto_routed: number
  tickets: Ticket[]
}

export interface Analytics {
  generated_at: string
  total: number
  open_count: number
  pending_agent_count: number
  resolved_count: number
  closed_count: number
  bot_solved_count: number
  human_solved_count: number
  automation_rate: number
  by_status: Record<string, number>
  by_queue: Record<string, number>
  by_channel: Record<string, number>
  channel_stats: {
    channel: string; total: number; open: number; assigned: number; resolved: number
    bot_solved: number; sla_breached: number
  }[]
  by_day: { date: string; count: number }[]
  sla_breached_count: number
  sla_unanswered_count: number
  sla_attainment_pct?: number | null
  sla_penalties_by_currency: Record<string, string>
  sla_by_tenant: { tenant_id: string; unanswered: number; breached: number; penalties: Record<string, number> }[]
  sla_at_risk: {
    tenant_id: string; ticket_id: string; status: string
    queue_id: string | null; rule_id: string | null
    age_hours: number; unanswered: boolean
    breach: { sla_rule_id?: string; currency?: string; penalty_amount?: string; unanswered_hours?: number } | null
  }[]
  recent: Ticket[]
}

export interface SlaTier {
  sla_rule_id?: string; currency?: string; penalty_amount?: string; unanswered_hours?: number
}
export interface SlaEval {
  tenant_id: string; ticket_id: string; status: string
  queue_id: string | null; rule_id: string | null
  age_hours: number; unanswered: boolean
  breach: SlaTier | null
  upcoming: SlaTier | null
  month?: string
}
export interface SlaReport {
  sla_policy_version: string
  total_tickets: number
  unanswered_count: number
  breached_count: number
  penalties_by_currency: Record<string, string>
  evaluations: SlaEval[]
}
export interface SlaQuarterRow {
  tenant_id: string | null; quarter: string; year: number
  tickets: number; open: number; unanswered: number; breached: number
  penalties: Record<string, number>
}
export interface SlaQuarters {
  year: number; years: number[]
  quarters: SlaQuarterRow[]
  by_tenant: SlaQuarterRow[]
}

export interface PolicyRule {
  id: string; status: string; tenant_id?: string | null
  region?: string | null; queue_id: string; lane: string
  pattern?: string; signals?: string[]; reason_codes?: string[]
  valid_from?: string | null; valid_to?: string | null
}
export interface TenantContext {
  display_name?: string; region?: string; notes?: string
  queue_hints?: Record<string, string>; priority_guidance?: string[]
  kb_hints?: { kb_id: string; topics?: string[]; years?: number[]; note?: string }[]
}
export interface Policy {
  policy_version: string
  rules: PolicyRule[]
  tenant_contexts?: Record<string, TenantContext>
  ticket_statuses?: string[]
}

export interface SlaRule {
  id: string; status: string; effective_status?: string
  tenant_id?: string | null; region?: string | null
  unanswered_hours: number; currency: string; penalty_amount: string
  applies_to_statuses?: string[]; applies_to_priorities?: string[]; valid_from?: string | null; valid_to?: string | null
  description?: string
}

export interface CostTenant { tenant_id: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: string }
export interface CostModel { model_id: string; kind: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: string }
export interface CostDay { date: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: string }
export interface CostTicket { tenant_id: string; ticket_id: string; kind: string; model_id: string | null; calls: number; input_tokens: number; output_tokens: number; cost_usd: string; last_call_at: string | null }
export interface Costs { by_tenant: CostTenant[]; by_model: CostModel[]; by_day: CostDay[]; by_ticket: CostTicket[]; total_cost_usd: string }
export interface CsatTenant {
  tenant_id: string; total_tickets: number; rated: number; response_rate: number
  avg_csat: number | null; distribution: Record<string, number>
  progress_delta: number | null; earlier_avg: number | null; recent_avg: number | null
  decile: number | null
  by_day: { date: string; avg: number; count: number }[]
}
export interface CsatReport {
  generated_at: string
  overall: { avg_csat: number | null; rated: number; total_tickets: number }
  tenants: CsatTenant[]
  top_by_score: CsatTenant[]
  top_by_progress: CsatTenant[]
}
export interface CsatQuarterRow {
  tenant_id: string | null; quarter: string; year: number
  tickets: number; rated: number; response_rate: number
  avg_csat: number | null; distribution: Record<string, number>
}
export interface CsatQuarters {
  year: number; years: number[]
  quarters: CsatQuarterRow[]
  by_tenant: CsatQuarterRow[]
}

export interface Reply { reply_id: string; author: string; body: string; created_at: string }
export interface Analysis {
  analysis_id: string; kind: string; canonical_version: number
  payload: Record<string, any>; model_id?: string | null; created_at: string
}
export interface TicketDetail {
  ticket: Ticket
  canonical: Record<string, any> | null
  decision: Record<string, any> | null
  sla: Record<string, any> | null
  replies: Reply[]
  analysis: Analysis | null
  llm_answer: Analysis | null
  llm_queue: Analysis | null
  llm_cost?: { calls: number; input_tokens: number; output_tokens: number; cost_usd: string } | null
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (body as any)?.detail
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return body as T
}

const qs = (tenant?: string) => (tenant ? `?tenant_id=${encodeURIComponent(tenant)}` : '')
const post = (url: string, payload?: any) =>
  req<any>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload ?? {}) })
const put = (url: string, payload: any) =>
  req<any>(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })

export const api = {
  health: () => req<{ status: string }>('/health'),
  summary: (tenant?: string) => req<Summary>(`/v1/dashboard/summary${qs(tenant)}`),
  analytics: (tenant?: string) => req<Analytics>(`/v1/dashboard/analytics${qs(tenant)}`),
  incidents: () => req<{ incidents: any[] }>('/v1/incident/recent'),
  sla: (tenant?: string) => req<SlaReport>(`/v1/dashboard/sla${qs(tenant)}`),
  slaQuarters: (year?: number, tenant?: string) =>
    req<SlaQuarters>(`/v1/dashboard/sla/quarters${qs(tenant)}${year ? `${tenant ? '&' : '?'}year=${year}` : ''}`),
  costs: () => req<Costs>('/v1/dashboard/costs'),
  csat: () => req<CsatReport>('/v1/dashboard/csat'),
  submitCsat: (t: string, id: string, score: number, comment?: string) =>
    post(`/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/csat`,
      { score, comment }),
  csatQuarters: (year?: number, tenant?: string) => {
    const p = new URLSearchParams()
    if (tenant) p.set('tenant_id', tenant)
    if (year) p.set('year', String(year))
    const s = p.toString()
    return req<CsatQuarters>(`/v1/dashboard/csat/quarters${s ? `?${s}` : ''}`)
  },
  kbArticles: () => req<{ snapshot_version: string; articles: any[] }>('/v1/dashboard/kb/articles'),
  createKbArticle: (p: any) => post('/v1/dashboard/kb/articles', p),
  updateKbArticle: (id: string, p: any) => put(`/v1/dashboard/kb/articles/${encodeURIComponent(id)}`, p),
  rebuildKb: () => post('/v1/dashboard/kb/rebuild'),
  kbDocs: () => req<{ docs: KbDocMeta[] }>('/v1/dashboard/kb/docs'),
  kbDoc: (file: string) =>
    req<KbDoc>(`/v1/dashboard/kb/docs/${encodeURIComponent(file)}`),
  dataRoomFiles: () => req<{ root: string; files: DataRoomFile[] }>('/v1/dashboard/dataroom/files'),
  dataRoomFile: (path: string) =>
    req<DataRoomFileContent>(`/v1/dashboard/dataroom/file?path=${encodeURIComponent(path)}`),
  slaRules: () => req<{ sla_policy_version: string; rules: SlaRule[] }>('/v1/dashboard/sla/rules'),
  createSlaRule: (p: any) => post('/v1/dashboard/sla/rules', p),
  updateSlaRule: (id: string, p: any) => put(`/v1/dashboard/sla/rules/${encodeURIComponent(id)}`, p),
  deleteSlaRule: (id: string) =>
    req<{ deleted: string; rules_remaining: number }>(`/v1/dashboard/sla/rules/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  policy: () => req<Policy>('/v1/routing/policy'),
  updateRule: (id: string, p: any) =>
    req<PolicyRule>(`/v1/routing/rules/${encodeURIComponent(id)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p),
    }),
  upsertTenantContext: (tenantId: string, p: any) =>
    req<any>(`/v1/routing/tenant-contexts/${encodeURIComponent(tenantId)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p),
    }),
  classify: (p: { text: string; region: string; tenant_id?: string | null; p1?: boolean; assist?: boolean }) =>
    req<any>('/v1/routing/classify', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p),
    }),
  ticket: (tenant: string, id: string) =>
    req<TicketDetail>(`/v1/dashboard/tickets/${encodeURIComponent(tenant)}/${encodeURIComponent(id)}`),
  ticketAttachments: (t: string, id: string) =>
    req<{ attachments: any[] }>(`/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/attachments`),
  attachmentContentUrl: (t: string, id: string, attId: string) =>
    `/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/attachments/${encodeURIComponent(attId)}/content`,
  ticketStatus: (t: string, id: string, status: string) =>
    req<Ticket>(`/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/status`,
      { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) }),
  reply: (t: string, id: string, author: 'agent' | 'customer' | 'bot', body: string) =>
    post(`/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/reply`, { author, body }),
  reopen: (t: string, id: string) =>
    post(`/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/reopen`),
  analyze: (t: string, id: string) =>
    post(`/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/analyze`),
  suggestQueue: (t: string, id: string) =>
    post(`/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/suggest-queue`),
  draft: (t: string, id: string) =>
    post(`/v1/dashboard/tickets/${encodeURIComponent(t)}/${encodeURIComponent(id)}/draft`),
  process: (payload: any) => post('/v1/pipeline/process', payload),
}

export interface KbDocMeta {
  file: string; kb_id: string; title: string
  topic: string; region: string; size: number
}
export interface KbDoc extends KbDocMeta { content: string }
export interface DataRoomFile {
  path: string; size: number; modified: number
}
export interface DataRoomFileContent {
  path: string; size: number; content: string
}

export const TENANT_STATUS_OPTIONS = ['open', 'in_progress', 'pending_customer', 'resolved', 'closed']

export function b64(s: string) {
  return btoa(String.fromCharCode(...new TextEncoder().encode(s)))
}

export function tenantLabel(id: string, names: Record<string, string>) {
  const n = names[id]
  return n ? `${n} (${id})` : id
}
