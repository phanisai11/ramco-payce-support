import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { RefreshCw, Plus, Pencil, CheckCircle2, Upload } from 'lucide-react'
import { api, type KbDoc, type KbDocMeta } from '../api'
import { Badge, Card, Empty, Modal, useTable, Th } from '../components/ui'
import { Markdown } from '../components/Markdown'

const inputCls = 'w-full rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 text-[12px] outline-none focus:border-brand-500'
const labelCls = 'mb-1 block text-[11px] font-semibold text-ink-500'

interface Article {
  id: string; title: string; topic?: string; region?: string
  excerpt?: string; status?: string; owner?: string; last_reviewed?: string
  valid_from?: string; valid_to?: string; years?: number[]
  helpful_pct?: number; views_90d?: number
  retrievable?: boolean; chunk_ids?: string[]
}

interface Form {
  id: string; title: string; topic: string; excerpt: string
  region: string; status: string; owner: string
  valid_from: string; valid_to: string; years: string
}

const EMPTY: Form = {
  id: '', title: '', topic: '', excerpt: '',
  region: 'ALL', status: 'Published', owner: '',
  valid_from: '', valid_to: '', years: '',
}

function articleToForm(a: Article): Form {
  return {
    id: a.id ?? '',
    title: a.title ?? '',
    topic: a.topic ?? 'general',
    excerpt: a.excerpt ?? '',
    region: a.region ?? 'ALL',
    status: a.status ?? 'Published',
    owner: a.owner ?? '',
    valid_from: (a.valid_from ?? '').slice(0, 10),
    valid_to: (a.valid_to ?? '').slice(0, 10),
    years: (a.years ?? []).join(', '),
  }
}

function windowLabel(a: Article): string {
  if (a.valid_from || a.valid_to) return `${(a.valid_from ?? '…').slice(0, 10)} → ${(a.valid_to ?? '…').slice(0, 10)}`
  if (a.years?.length) return `FY ${Math.min(...a.years)}–${Math.max(...a.years)}`
  return 'always'
}

function parseMdFile(text: string): { meta: Record<string, string>; body: string } {
  const meta: Record<string, string> = {}
  let body = text
  if (text.startsWith('---')) {
    const end = text.indexOf('\n---', 3)
    if (end !== -1) {
      for (const line of text.slice(3, end).trim().split('\n')) {
        const i = line.indexOf(':')
        if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, '')
      }
      body = text.slice(end + 4).replace(/^\n+/, '')
    }
  }
  return { meta, body }
}

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/
function idFromFile(name: string, meta: Record<string, string>): string {
  const cand = (meta.kb_id || name.replace(/\.md$/i, '')).trim()
  return ID_RE.test(cand) ? cand : `KB-${cand.replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 40)}`
}

function ArticleForm({ form, setForm, isEdit }: {
  form: Form; setForm: (f: Form) => void; isEdit?: boolean
}) {
  const set = (k: keyof Form, v: string) => setForm({ ...form, [k]: v })
  const fileRef = useRef<HTMLInputElement>(null)
  const [mdName, setMdName] = useState('')

  const onMdFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    f.text().then((text) => {
      const { meta, body } = parseMdFile(text)
      setMdName(f.name)
      setForm({
        ...form,
        id: form.id || idFromFile(f.name, meta),
        title: meta.title || form.title || f.name.replace(/\.md$/i, ''),
        topic: meta.topic || form.topic || 'general',
        region: ['IN', 'UAE', 'ALL'].includes(meta.region) ? meta.region : form.region,
        excerpt: body || form.excerpt,
        // New articles from .md start as Draft so they can be reviewed
        // and edited before publishing; edits keep their current status.
        status: isEdit ? form.status : 'Draft',
      })
    }).catch(() => {})
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <label className="block sm:col-span-2">
        <span className={labelCls}>Attach .md file (optional — fills fields from front matter, sets Draft)</span>
        <input ref={fileRef} type="file" accept=".md,.markdown,.txt" onChange={onMdFile} className="hidden" />
        <button type="button" onClick={() => fileRef.current?.click()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-ink-300 bg-ink-50 px-3 py-2 text-[12px] font-semibold text-ink-600 hover:border-brand-400 hover:text-brand-600">
          <Upload size={13} /> {mdName || 'Choose markdown file…'}
        </button>
      </label>
      <label className="block"><span className={labelCls}>Article ID</span>
        <input value={form.id} onChange={(e) => set('id', e.target.value)} disabled={isEdit}
          className={`${inputCls} disabled:bg-ink-50 disabled:text-ink-400`} placeholder="KB-0201" /></label>
      <label className="block"><span className={labelCls}>Topic (RAG kb_id)</span>
        <input value={form.topic} onChange={(e) => set('topic', e.target.value)} className={inputCls} placeholder="pf_wage_ceiling" /></label>
      <label className="block sm:col-span-2"><span className={labelCls}>Title</span>
        <input value={form.title} onChange={(e) => set('title', e.target.value)} className={inputCls} /></label>
      <label className="block sm:col-span-2"><span className={labelCls}>Content / excerpt (markdown supported — this is what RAG retrieves)</span>
        <textarea value={form.excerpt} onChange={(e) => set('excerpt', e.target.value)} rows={8} className={`${inputCls} resize-y font-mono text-[12px]`} /></label>
      <label className="block"><span className={labelCls}>Region</span>
        <select value={form.region} onChange={(e) => set('region', e.target.value)} className={inputCls}>
          <option value="ALL">ALL — both IN & UAE</option>
          <option value="IN">IN only</option>
          <option value="UAE">UAE only</option>
        </select></label>
      <label className="block"><span className={labelCls}>Status</span>
        <select value={form.status} onChange={(e) => set('status', e.target.value)} className={inputCls}>
          <option value="Published">Published (retrievable)</option>
          <option value="Draft">Draft (hidden from RAG)</option>
          <option value="Archived">Archived (hidden from RAG)</option>
        </select></label>
      <label className="block"><span className={labelCls}>Valid from</span>
        <input value={form.valid_from} onChange={(e) => set('valid_from', e.target.value)} type="date" className={inputCls} /></label>
      <label className="block"><span className={labelCls}>Valid to</span>
        <input value={form.valid_to} onChange={(e) => set('valid_to', e.target.value)} type="date" className={inputCls} /></label>
      <label className="block sm:col-span-2"><span className={labelCls}>Years (comma-separated, e.g. 2025, 2026) — used when no explicit dates</span>
        <input value={form.years} onChange={(e) => set('years', e.target.value)} className={inputCls} placeholder="2025, 2026" /></label>
      <label className="block"><span className={labelCls}>Owner</span>
        <input value={form.owner} onChange={(e) => set('owner', e.target.value)} className={inputCls} /></label>
    </div>
  )
}

function stripFrontMatter(md: string): string {
  if (!md.startsWith('---')) return md
  const end = md.indexOf('\n---', 3)
  return end === -1 ? md : md.slice(end + 4).replace(/^\n+/, '')
}

const STOP = new Set(['the', 'and', 'for', 'with', 'how', 'what', 'does', 'article'])
function tokens(s: string): Set<string> {
  return new Set(s.toLowerCase().split(/[^a-z0-9]+/).filter((t) => t.length >= 3 && !STOP.has(t)))
}

// Best kb_doc match for an article via token overlap across
// article title+topic vs doc title+topic+kb_id+filename.
function bestDocFor(a: Article, docs: KbDocMeta[]): KbDocMeta | null {
  const at = tokens(`${a.title} ${a.topic ?? ''} ${a.id}`)
  let best: KbDocMeta | null = null
  let bestScore = 0
  for (const d of docs) {
    const dt = tokens(`${d.title} ${d.topic} ${d.kb_id} ${d.file}`)
    let score = 0
    for (const t of at) if (dt.has(t)) score++
    if (score > bestScore) { bestScore = score; best = d }
  }
  return bestScore >= 2 ? best : null
}

export function Kb() {
  const [data, setData] = useState<{ snapshot_version: string; articles: Article[] } | null>(null)
  const [docs, setDocs] = useState<KbDocMeta[]>([])
  const [viewing, setViewing] = useState<KbDoc | null>(null)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Article | null>(null)
  const [form, setForm] = useState<Form>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [filter, setFilter] = useState('')
  const [toast, setToast] = useState('')

  const load = useCallback(() => {
    api.kbArticles().then(setData).catch(() => setData(null))
    api.kbDocs().then((r) => setDocs(r.docs)).catch(() => setDocs([]))
  }, [])
  useEffect(() => { load() }, [load])

  const viewDoc = async (file: string) => {
    try { setViewing(await api.kbDoc(file)) }
    catch { setViewing(null) }
  }

  const [viewingArticle, setViewingArticle] = useState<Article | null>(null)
  const openArticle = (a: Article) => {
    const d = bestDocFor(a, docs)
    if (d) void viewDoc(d.file)
    else setViewingArticle(a)
  }
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 3500)
    return () => clearTimeout(t)
  }, [toast])

  const payloadFor = (f: Form): any => {
    const p: any = {
      id: f.id.trim(),
      title: f.title.trim(),
      topic: f.topic.trim() || 'general',
      excerpt: f.excerpt.trim(),
      region: f.region,
      status: f.status,
    }
    if (f.owner.trim()) p.owner = f.owner.trim()
    if (f.valid_from) p.valid_from = f.valid_from
    if (f.valid_to) p.valid_to = f.valid_to
    const years = f.years.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n))
    if (years.length) p.years = years
    return p
  }
  const payload = () => payloadFor(form)

  const unpublish = async (a: Article) => {
    setBusy(true); setMsg('')
    try {
      await api.updateKbArticle(a.id, payloadFor({ ...articleToForm(a), status: 'Archived' }))
      setToast(`${a.id} unpublished — status set to Archived`)
      await load()
    } catch (e: any) { setMsg(`Error: ${e.message}`) }
    setBusy(false)
  }

  const save = async () => {
    setBusy(true); setMsg('')
    try {
      if (editing) {
        await api.updateKbArticle(editing.id, payload())
        setToast(`DB updated — ${editing.id} saved and RAG reloaded`)
        setEditing(null)
      } else {
        await api.createKbArticle(payload())
        setToast(`DB updated — ${form.id.trim()} saved and RAG reloaded`)
        setAdding(false)
      }
      setForm(EMPTY); await load()
    } catch (e: any) { setMsg(`Error: ${e.message}`) }
    setBusy(false)
  }

  const articles = (data?.articles ?? []).filter((a) =>
    !filter || `${a.id} ${a.title} ${a.topic ?? ''}`.toLowerCase().includes(filter.toLowerCase()))
  const tbl = useTable(articles, {
    article: (a) => `${a.id} ${a.title}`,
    topic: (a) => a.topic ?? 'general',
    region: (a) => a.region ?? 'ALL',
    effective: (a) => windowLabel(a),
    chunks: (a) => (a.chunk_ids ?? []).length,
    status: (a) => a.status ?? 'Published',
    owner: (a) => a.owner ?? '',
  })

  const modalShell = (title: string, onClose: () => void, isEdit?: boolean) => (
    <Modal onClose={onClose} title={title} wide>
      <p className="mb-3 text-[12px] text-ink-500">
        Saved to Postgres (<code className="rounded bg-ink-100 px-1">app.kb_article</code>) and hot-reloaded into RAG.
        Attach a <code className="rounded bg-ink-100 px-1 text-[11px]">.md</code> file to import a full document —
        it fills the fields from front matter and saves as <b>Draft</b> for review. Only <b>Published</b> items are retrievable;
        published articles are read-only.
      </p>
      <ArticleForm form={form} setForm={setForm} isEdit={isEdit} />
      {msg && <div className="mt-3 rounded-lg border border-error-200 bg-error-50 px-3 py-2 text-[12px] text-error-600">{msg}</div>}
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="rounded-lg border border-ink-200 px-3.5 py-1.5 text-xs font-semibold text-ink-600 hover:bg-ink-100">Cancel</button>
        <button onClick={save} disabled={busy || !form.id.trim() || !form.title.trim() || !form.excerpt.trim()}
          className="rounded-lg bg-brand-500 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
          {busy ? 'Saving…' : isEdit ? 'Save changes' : 'Add article'}
        </button>
      </div>
    </Modal>
  )

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-ink-900">Knowledge base</h2>
          <p className="mt-0.5 max-w-2xl text-[13px] text-ink-500">
            Articles backing RAG retrieval — stored in Postgres (<code className="rounded bg-ink-100 px-1 text-[11px]">app.kb_article</code>),
            region-scoped, validity-windowed, tenant-scoped via topic hints. Only <b>Published</b> items are retrievable.
            {data && <> Snapshot <code className="rounded bg-ink-100 px-1 text-[11px]">{data.snapshot_version}</code>.</>}
          </p>
        </div>
        <div className="flex gap-2">
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter articles…"
            className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-[12px] outline-none focus:border-brand-500" />
          <button onClick={load} className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 bg-white text-ink-500 hover:bg-ink-100">
            <RefreshCw size={14} />
          </button>
          <button onClick={() => { setForm(EMPTY); setMsg(''); setEditing(null); setAdding(true) }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-500 px-3.5 py-2 text-xs font-semibold text-white hover:bg-brand-600">
            <Plus size={14} /> Add article
          </button>
        </div>
      </div>

      <Card title={`Articles (${articles.length})`} className="overflow-x-auto">
        {!data && <Empty text="Loading…" />}
        {data && (
          <table className="w-full min-w-[62rem] text-[13px]">
            <thead>
              <tr className="border-b border-ink-200 text-left text-ink-500">
                <Th t={tbl} k="article" className="px-3 py-3">Article</Th>
                <Th t={tbl} k="topic" className="px-3 py-3">Topic</Th>
                <Th t={tbl} k="region" className="px-3 py-3">Region</Th>
                <Th t={tbl} k="effective" className="px-3 py-3">Effective</Th>
                <Th t={tbl} k="chunks" className="px-3 py-3">RAG chunks</Th>
                <Th t={tbl} k="status" className="px-3 py-3">Status</Th>
                <Th t={tbl} k="owner" className="px-3 py-3">Owner</Th>
                <th className="px-3 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {tbl.visible.map((a) => (
                <tr key={a.id} className="border-b border-ink-50 align-top hover:bg-ink-50/50">
                  <td className="px-3 py-3">
                    <button onClick={() => openArticle(a)} title="View article content"
                      className="font-mono text-[12px] font-semibold text-brand-600 hover:underline">
                      {a.id}
                    </button>
                    <button onClick={() => openArticle(a)}
                      className="mt-0.5 block max-w-[18rem] text-left text-[12px] font-medium text-ink-800 hover:text-brand-600">
                      {a.title}
                    </button>
                    {a.excerpt ? <div className="mt-0.5 max-w-[18rem] truncate text-[11px] text-ink-400">{a.excerpt}</div> : null}
                  </td>
                  <td className="px-3 py-3"><Badge tone="in_progress">{a.topic ?? 'general'}</Badge></td>
                  <td className="px-3 py-3 text-[12px] text-ink-600">{a.region ?? 'ALL'}</td>
                  <td className="px-3 py-3 text-[11px] tabular-nums text-ink-500">{windowLabel(a)}</td>
                  <td className="px-3 py-3">
                    {a.retrievable
                      ? <span className="flex flex-wrap gap-1">{(a.chunk_ids ?? []).map((c) => (
                          <code key={c} className="rounded bg-emerald-50 px-1 py-0.5 text-[10px] text-emerald-700">{c}</code>))}</span>
                      : <span className="text-[11px] text-ink-400">not retrievable</span>}
                  </td>
                  <td className="px-3 py-3"><Badge tone={a.status === 'Published' ? 'approved' : 'outdated'}>{a.status ?? 'Published'}</Badge></td>
                  <td className="px-3 py-3 text-[11px] text-ink-500">{a.owner ?? '—'}</td>
                  <td className="px-3 py-3">
                    {(a.status ?? 'Published') === 'Published' ? (
                      <button onClick={() => unpublish(a)} disabled={busy}
                        className="rounded-lg border border-ink-200 px-2 py-1 text-[11px] font-semibold text-ink-500 hover:bg-ink-100 disabled:opacity-50"
                        title="Published articles are read-only — archive to unpublish">
                        Archive
                      </button>
                    ) : (
                      <button onClick={() => { setMsg(''); setEditing(a); setForm(articleToForm(a)); setAdding(false) }}
                        className="rounded-lg border border-ink-200 p-1.5 text-ink-500 hover:bg-ink-100 hover:text-brand-500" title="Edit article">
                        <Pencil size={13} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {!tbl.visible.length && <tr><td colSpan={8}><Empty text="No articles match." /></td></tr>}
            </tbody>
          </table>
        )}
      </Card>

      {viewingArticle && (
        <Modal onClose={() => setViewingArticle(null)} title={viewingArticle.title} wide>
          <div className="mb-3 flex items-center gap-2 text-[11px] text-ink-400">
            <code>{viewingArticle.id}</code>
            {viewingArticle.topic && <Badge tone="draft">{viewingArticle.topic}</Badge>}
            {viewingArticle.region && <Badge tone="approved">{viewingArticle.region}</Badge>}
            <Badge tone={viewingArticle.status === 'Published' ? 'approved' : 'outdated'}>{viewingArticle.status ?? 'Published'}</Badge>
          </div>
          <div className="max-h-[60vh] overflow-y-auto rounded-lg border border-ink-100 bg-white p-4 text-[13px] leading-relaxed text-ink-700">
            {viewingArticle.excerpt
              ? <p className="whitespace-pre-wrap">{viewingArticle.excerpt}</p>
              : <Empty text="No excerpt stored for this article." />}
            <p className="mt-3 text-[11px] text-ink-400">
              Full detailed content for this topic lives in the linked kb_doc when one exists — no matching document was found for this article.
            </p>
          </div>
        </Modal>
      )}

      {viewing && (
        <Modal onClose={() => setViewing(null)} title={viewing.title} wide>
          <div className="mb-2 flex items-center gap-2 text-[11px] text-ink-400">
            <code>{viewing.file}</code>
            {viewing.kb_id && <Badge tone="in_progress">{viewing.kb_id}</Badge>}
            {viewing.topic && <Badge tone="draft">{viewing.topic}</Badge>}
            {viewing.region && <Badge tone="approved">{viewing.region}</Badge>}
          </div>
          <div className="max-h-[65vh] overflow-y-auto rounded-lg border border-ink-100 bg-white p-4">
            <Markdown text={stripFrontMatter(viewing.content)} />
          </div>
        </Modal>
      )}

      {adding && modalShell('Add KB article', () => setAdding(false))}
      {editing && modalShell(`Edit KB article ${editing.id}`, () => setEditing(null), true)}

      {toast && (
        <div className="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-xl border border-success-500/30 bg-white px-4 py-3 text-[13px] font-medium text-ink-800 shadow-pop">
          <CheckCircle2 size={16} className="text-success-500" /> {toast}
        </div>
      )}
    </div>
  )
}
