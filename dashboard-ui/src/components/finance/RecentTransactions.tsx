import { useState } from 'react'
import type { RecentTransaction } from '../../api/types'
import { fmtSignedCcy } from '../../lib/format'

interface Props { transactions: RecentTransaction[] }

const CATEGORY_COLORS: Record<string, string> = {
  'good-life': '#ec4899',
  'personal': '#6366f1',
  'home': '#f59e0b',
  'transport': '#14b8a6',
  'groceries': '#22c55e',
  'eating-out': '#ef4444',
  'entertainment': '#a855f7',
  'health': '#06b6d4',
  'education': '#84cc16',
  'income': '#22c55e',
  'transfer': '#a1a1aa',
}

function getCategoryColor(category?: string): string {
  if (!category) return '#a1a1aa'
  const cat = String(category).toLowerCase()
  return CATEGORY_COLORS[cat] ?? CATEGORY_COLORS[cat.replace(/\s+/g, '-')] ?? '#a1a1aa'
}

function fmtRelativeDate(date?: string): string {
  if (!date) return '\u2014'
  const d = new Date(date)
  if (Number.isNaN(d.getTime())) return '\u2014'
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7) return `${diffDays} days ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
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
          return (
            <div
              key={i}
              className={`flex items-center justify-between px-4 py-3 hover:bg-[var(--color-surface-alt)]/50 transition-colors ${
                i % 2 === 1 ? 'bg-[var(--color-surface-alt)]/25' : ''
              } ${i > 0 ? 'border-t border-[var(--color-border)]/30' : ''}`}
            >
              <div className="flex items-center gap-3 flex-1 min-w-0">
                <span
                  className="inline-block rounded-full shrink-0"
                  style={{ width: 8, height: 8, backgroundColor: catColor }}
                />
                <div className="min-w-0 flex-1">
                  <div className="text-sm truncate">{tx.description ?? '\u2014'}</div>
                  <div className="text-xs text-[var(--color-text-muted)] font-mono mt-0.5 flex items-center gap-2">
                    <span>{fmtRelativeDate(tx.date)}</span>
                    <span className="opacity-50">·</span>
                    <span
                      className="rounded-full px-1.5 py-0 text-[10px]"
                      style={{ backgroundColor: `${catColor}20`, color: catColor }}
                    >
                      {tx.parent_category ?? tx.category ?? '\u2014'}
                    </span>
                  </div>
                </div>
              </div>
              <div className={`font-mono text-sm ml-3 tabular-nums font-medium ${
                (tx.amount ?? 0) > 0 ? 'text-[var(--color-green)]' : ''
              }`}>
                {fmtSignedCcy(tx.amount)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
