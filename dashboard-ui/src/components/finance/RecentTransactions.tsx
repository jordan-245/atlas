import { useState } from 'react'
import type { RecentTransaction } from '../../api/types'
import { fmtSignedCcy, fmtRelativeTime, fmtDateShort } from '../../lib/format'

interface Props { transactions: RecentTransaction[] }

const CATEGORY_COLORS: Record<string, string> = {
  'good-life':     '#ec4899',
  'personal':      '#6366f1',
  'home':          '#f59e0b',
  'transport':     '#14b8a6',
  'groceries':     '#22c55e',
  'eating-out':    '#ef4444',
  'entertainment': '#a855f7',
  'health':        '#06b6d4',
  'education':     '#84cc16',
  'income':        '#22c55e',
  'transfer':      '#a1a1aa',
}

function getCategoryColor(category?: string): string {
  if (!category) return '#a1a1aa'
  const cat = String(category).toLowerCase()
  return CATEGORY_COLORS[cat] ?? CATEGORY_COLORS[cat.replace(/\s+/g, '-')] ?? '#a1a1aa'
}

function fmtDate(date?: string): { relative: string; absolute: string } {
  if (!date) return { relative: '—', absolute: '' }
  const d = new Date(date)
  if (Number.isNaN(d.getTime())) return { relative: '—', absolute: '' }
  const diffMs = Date.now() - d.getTime()
  const diffHrs = diffMs / (1000 * 60 * 60)
  const relative = diffHrs < 24 ? fmtRelativeTime(date) : fmtDateShort(date)
  const absolute = d.toLocaleDateString('en-AU', { year: 'numeric', month: 'short', day: 'numeric' })
  return { relative, absolute }
}

export function RecentTransactions({ transactions }: Props) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? transactions : transactions.slice(0, 10)

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold">
          RECENT TRANSACTIONS ({transactions.length})
        </div>
        {transactions.length > 10 ? (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] font-mono transition-colors"
          >
            {expanded ? 'Show less' : `Show all (${transactions.length})`}
          </button>
        ) : null}
      </div>
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        {visible.map((tx, i) => {
          const catColor = getCategoryColor(tx.parent_category ?? tx.category)
          const amount = tx.amount ?? 0
          const { relative, absolute } = fmtDate(tx.date)
          return (
            <div
              key={i}
              className={[
                'flex items-center justify-between px-4 py-3 transition-colors',
                'hover:bg-[var(--color-surface-alt)]/50',
                i % 2 === 1 ? 'bg-[var(--color-surface-alt)]/25' : '',
                i > 0 ? 'border-t border-[var(--color-border)]/30' : '',
              ].join(' ')}
            >
              <div className="flex items-center gap-3 flex-1 min-w-0">
                <span
                  className="inline-block rounded-full shrink-0"
                  style={{ width: 8, height: 8, backgroundColor: catColor }}
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="text-sm truncate">{tx.description ?? '—'}</div>
                  <div className="text-xs text-[var(--color-text-muted)] font-mono mt-0.5 flex items-center gap-2">
                    <span title={absolute || undefined}>{relative}</span>
                    <span className="opacity-50">·</span>
                    <span
                      className="rounded-full px-1.5 py-0 text-[10px] font-medium"
                      style={{ backgroundColor: `${catColor}20`, color: catColor }}
                    >
                      {tx.parent_category ?? tx.category ?? '—'}
                    </span>
                  </div>
                </div>
              </div>
              <div
                className="font-mono text-sm ml-3 tabular-nums font-medium"
                style={{ color: amount > 0 ? 'var(--color-green)' : amount < 0 ? 'var(--color-red)' : undefined }}
              >
                {fmtSignedCcy(amount)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
