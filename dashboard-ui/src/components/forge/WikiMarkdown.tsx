/**
 * WikiMarkdown — minimal, dependency-free markdown renderer for research-wiki pages.
 *
 * Builds React elements directly (never innerHTML), so it is XSS-safe by
 * construction: everything lands in the DOM as text nodes. Supports the subset
 * the wiki actually uses: headings, bold/italic/inline-code, fenced code blocks,
 * bullet/numbered lists, tables, blockquotes, [[wiki-links]] (rendered as chips),
 * and YAML frontmatter (rendered as a key/value grid).
 */
import { useQuery } from '@tanstack/react-query'
import { qk } from '../../api/keys'
import { Skeleton } from '../layout/Skeleton'

/** The wiki-page endpoint returns text/plain — the shared get() wrapper expects JSON. */
async function getText(url: string): Promise<string> {
  const res = await fetch(url, { credentials: 'same-origin' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.text()
}

export function useWikiPage(section: string | null, page: string | null) {
  return useQuery({
    queryKey: [...qk.forge.all(), 'wiki-page', section, page],
    queryFn: () => getText(`/api/forge/wiki-page?section=${encodeURIComponent(section!)}&page=${encodeURIComponent(page!)}`),
    enabled: !!section && !!page,
    staleTime: 5 * 60_000,
  })
}

// ── inline spans: **bold**, *italic*, `code`, [[wiki-link]] ──────────────────
const INLINE_RE = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\[\[[^\]]+\]\])/g

function renderInline(text: string, keyBase: string): React.ReactNode[] {
  const parts = text.split(INLINE_RE)
  return parts.map((part, i) => {
    const key = `${keyBase}-${i}`
    if (part.startsWith('**') && part.endsWith('**'))
      return <strong key={key} className="text-[var(--color-text)]">{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`'))
      return <code key={key} className="px-1 py-0.5 rounded bg-[var(--color-surface-alt)] text-[11px]">{part.slice(1, -1)}</code>
    if (part.startsWith('[[') && part.endsWith(']]'))
      return (
        <span key={key} className="px-1 py-0.5 rounded bg-[var(--color-surface-alt)] text-[11px] text-[var(--color-text)]">
          ⛓ {part.slice(2, -2)}
        </span>
      )
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2)
      return <em key={key}>{part.slice(1, -1)}</em>
    return part
  })
}

// ── block-level parser ───────────────────────────────────────────────────────
function parseBlocks(md: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  let lines = md.split('\n')
  let k = 0

  // frontmatter -> key/value grid
  if (lines[0]?.trim() === '---') {
    const end = lines.findIndex((l, i) => i > 0 && l.trim() === '---')
    if (end > 0) {
      const kvs = lines.slice(1, end)
        .map((l) => l.match(/^(\w+):\s*(.*)$/))
        .filter(Boolean) as RegExpMatchArray[]
      out.push(
        <div key={`fm-${k++}`} className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px] p-3 rounded-lg bg-[var(--color-surface-alt)] mb-3">
          {kvs.map((m, i) => (
            <div key={i} className="contents">
              <span className="text-[var(--color-text-muted)] uppercase tracking-wide text-[9px] self-center">{m[1]}</span>
              <span className="text-[var(--color-text)]">{m[2]}</span>
            </div>
          ))}
        </div>,
      )
      lines = lines.slice(end + 1)
    }
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    if (line.startsWith('```')) {                       // fenced code
      const close = lines.findIndex((l, j) => j > i && l.startsWith('```'))
      const body = lines.slice(i + 1, close === -1 ? undefined : close).join('\n')
      out.push(<pre key={k++} className="p-3 rounded-lg bg-[var(--color-surface-alt)] text-[11px] overflow-x-auto whitespace-pre-wrap">{body}</pre>)
      i = close === -1 ? lines.length : close + 1
      continue
    }

    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const lvl = h[1].length
      const cls = lvl === 1 ? 'text-base font-bold mt-1' : lvl === 2 ? 'text-sm font-bold mt-3' : 'text-[13px] font-bold mt-2'
      out.push(<div key={k++} className={`${cls} text-[var(--color-text)]`}>{renderInline(h[2], `h${k}`)}</div>)
      i++
      continue
    }

    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {             // list block
      const items: string[] = []
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ''))
        i++
      }
      out.push(
        <ul key={k++} className="space-y-1 pl-4">
          {items.map((it, j) => (
            <li key={j} className="text-xs leading-relaxed list-disc text-[var(--color-text-muted)]">{renderInline(it, `li${k}-${j}`)}</li>
          ))}
        </ul>,
      )
      continue
    }

    if (line.trimStart().startsWith('|')) {             // table block
      const rows: string[] = []
      while (i < lines.length && lines[i].trimStart().startsWith('|')) { rows.push(lines[i]); i++ }
      const cells = rows
        .filter((r) => !/^\s*\|[\s:-]+\|/.test(r) || !/^[\s|:-]+$/.test(r))
        .map((r) => r.split('|').slice(1, -1).map((c) => c.trim()))
        .filter((r) => r.some((c) => c && !/^[-:\s]+$/.test(c)))
      if (cells.length > 0) {
        out.push(
          <div key={k++} className="overflow-x-auto">
            <table className="text-[11px] w-full">
              <tbody>
                {cells.map((r, ri) => (
                  <tr key={ri} className={ri === 0 ? 'font-bold text-[var(--color-text)]' : 'text-[var(--color-text-muted)] border-t border-[var(--color-border)]'}>
                    {r.map((c, ci) => <td key={ci} className="px-2 py-1 align-top">{renderInline(c, `t${k}-${ri}-${ci}`)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>,
        )
      }
      continue
    }

    if (line.startsWith('>')) {                          // blockquote
      out.push(
        <blockquote key={k++} className="border-l-2 border-[var(--color-border)] pl-3 text-xs text-[var(--color-text-muted)] italic">
          {renderInline(line.replace(/^>\s?/, ''), `q${k}`)}
        </blockquote>,
      )
      i++
      continue
    }

    if (line.trim() === '') { i++; continue }

    // paragraph — merge consecutive prose lines
    const para: string[] = [line]
    i++
    while (i < lines.length && lines[i].trim() !== '' && !/^(#{1,4}\s|```|\s*[-*]\s|\s*\d+\.\s|\||>)/.test(lines[i])) {
      para.push(lines[i]); i++
    }
    out.push(<p key={k++} className="text-xs leading-relaxed text-[var(--color-text-muted)]">{renderInline(para.join(' '), `p${k}`)}</p>)
  }
  return out
}

export function WikiMarkdown({ section, page }: { section: string; page: string }) {
  const q = useWikiPage(section, page)
  if (q.isLoading) return <Skeleton className="h-40" />
  if (q.isError || typeof q.data !== 'string') {
    return <div className="text-xs text-[var(--color-negative)]">Couldn’t load {section}/{page}.md</div>
  }
  return <div className="space-y-2.5">{parseBlocks(q.data)}</div>
}
