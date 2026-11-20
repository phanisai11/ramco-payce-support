import { Fragment, type ReactNode } from 'react'

// Minimal Markdown renderer for KB docs / data-room files: headings,
// fenced code, lists, bold/inline-code, tables, paragraphs. Not a full
// spec — enough for the internal docs in this repo.

function inline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(<Fragment key={`${keyBase}-t${i}`}>{text.slice(last, m.index)}</Fragment>)
    const tok = m[0]
    if (tok.startsWith('`')) {
      nodes.push(<code key={`${keyBase}-c${i}`} className="rounded bg-ink-100 px-1 py-0.5 font-mono text-[0.85em] text-brand-700">{tok.slice(1, -1)}</code>)
    } else if (tok.startsWith('**')) {
      nodes.push(<strong key={`${keyBase}-b${i}`}>{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('*')) {
      nodes.push(<em key={`${keyBase}-i${i}`}>{tok.slice(1, -1)}</em>)
    } else {
      const lm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok)
      if (lm) nodes.push(<span key={`${keyBase}-a${i}`} className="text-brand-600 underline">{lm[1]}</span>)
    }
    last = m.index + tok.length
    i++
  }
  if (last < text.length) nodes.push(<Fragment key={`${keyBase}-t${i}`}>{text.slice(last)}</Fragment>)
  return nodes
}

function isTableRow(line: string) { return line.trim().startsWith('|') && line.trim().endsWith('|') }
function isTableSep(line: string) { return /^\|[\s:|-]+\|?$/.test(line.trim()) && line.includes('-') }

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const out: ReactNode[] = []
  let i = 0
  let key = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (trimmed.startsWith('```')) {
      const buf: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) { buf.push(lines[i]); i++ }
      i++
      out.push(
        <pre key={key++} className="my-2 overflow-x-auto rounded-lg bg-ink-900 p-3 text-[12px] leading-relaxed text-ink-100">
          <code>{buf.join('\n')}</code>
        </pre>,
      )
      continue
    }

    const h = /^(#{1,4})\s+(.*)$/.exec(trimmed)
    if (h) {
      const lvl = h[1].length
      const cls = lvl === 1 ? 'text-lg font-bold' : lvl === 2 ? 'mt-4 text-[15px] font-bold' : 'mt-3 text-[13px] font-semibold'
      out.push(<div key={key++} className={`${cls} text-ink-900`}>{inline(h[2], `h${key}`)}</div>)
      i++
      continue
    }

    if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const header = line.trim().slice(1, -1).split('|').map((c) => c.trim())
      const rows: string[][] = []
      i += 2
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(lines[i].trim().slice(1, -1).split('|').map((c) => c.trim()))
        i++
      }
      out.push(
        <div key={key++} className="my-2 overflow-x-auto rounded-lg border border-ink-200">
          <table className="w-full text-[12px]">
            <thead><tr className="bg-ink-50 text-left text-ink-500">
              {header.map((c, j) => <th key={j} className="px-2.5 py-1.5 font-semibold">{inline(c, `th${key}-${j}`)}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-t border-ink-100">
                  {r.map((c, ci) => <td key={ci} className="px-2.5 py-1.5 text-ink-700">{inline(c, `td${key}-${ri}-${ci}`)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    if (/^[-*]\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed)) {
      const ordered = /^\d+\.\s+/.test(trimmed)
      const items: string[] = []
      while (i < lines.length && (/^[-*]\s+/.test(lines[i].trim()) || /^\d+\.\s+/.test(lines[i].trim()))) {
        items.push(lines[i].trim().replace(/^[-*]\s+|^\d+\.\s+/, ''))
        i++
      }
      out.push(
        ordered
          ? <ol key={key++} className="my-1 list-decimal space-y-0.5 pl-5 text-[13px] text-ink-700">{items.map((it, j) => <li key={j}>{inline(it, `li${key}-${j}`)}</li>)}</ol>
          : <ul key={key++} className="my-1 list-disc space-y-0.5 pl-5 text-[13px] text-ink-700">{items.map((it, j) => <li key={j}>{inline(it, `li${key}-${j}`)}</li>)}</ul>,
      )
      continue
    }

    if (trimmed === '') { i++; continue }
    if (trimmed === '---') { i++; continue }

    // paragraph: gather until blank line or block start
    const buf: string[] = [line]
    i++
    while (i < lines.length && lines[i].trim() !== '' && !/^(#{1,4}\s|```|[-*]\s|\d+\.\s)/.test(lines[i].trim()) && !isTableRow(lines[i])) {
      buf.push(lines[i]); i++
    }
    out.push(<p key={key++} className="my-1.5 text-[13px] leading-relaxed text-ink-700">{inline(buf.join(' '), `p${key}`)}</p>)
  }
  return <div className="text-[13px]">{out}</div>
}
