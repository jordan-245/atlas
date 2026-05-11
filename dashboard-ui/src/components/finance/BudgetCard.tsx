import { fmtCcy } from '../../lib/format'

interface Props {
  name: string
  limit: number
  spent: number
}

// Token-driven color selection for the ring and progress bar
function ringColor(pct: number, over: boolean): string {
  if (over || pct > 100) return 'var(--color-red)'
  if (pct > 80) return 'var(--color-amber, #f59e0b)'
  return 'var(--color-green)'
}

export function BudgetCard({ name, limit, spent }: Props) {
  const rawPct = (spent / limit) * 100
  const pct = Math.min(100, rawPct)
  const remaining = limit - spent
  const over = spent > limit
  const color = ringColor(rawPct, over)

  const radius = 32
  const circumference = 2 * Math.PI * radius
  const offset = circumference * (1 - pct / 100)

  return (
    <div data-testid="budget-card" className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)] flex items-center gap-4 dash-card">
      <svg width={80} height={80} className="shrink-0">
        <circle cx={40} cy={40} r={radius} fill="none" stroke="var(--color-border)" strokeWidth={6} />
        <circle cx={40} cy={40} r={radius} fill="none" stroke={color} strokeWidth={6}
          strokeDasharray={circumference} strokeDashoffset={offset}
          strokeLinecap="round" transform="rotate(-90 40 40)"
          style={{ transition: 'stroke-dashoffset 0.8s cubic-bezier(0.16,1,0.3,1)' }} />
        <text x={40} y={40} textAnchor="middle" dominantBaseline="central"
          className="fill-[var(--color-text)] font-mono font-semibold" style={{ fontSize: 13 }}>
          {rawPct.toFixed(0)}%
        </text>
      </svg>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] font-medium truncate" title={name}>{name}</div>
        <div className="mt-2 space-y-1">
          <div className="flex justify-between text-xs font-mono tabular-nums">
            <span className="text-[var(--color-text-muted)]">Spent</span>
            <span>{fmtCcy(spent)}</span>
          </div>
          <div className="flex justify-between text-xs font-mono tabular-nums">
            <span className="text-[var(--color-text-muted)]">{over ? 'Over' : 'Left'}</span>
            <span className="font-semibold" style={{ color: over ? 'var(--color-red)' : 'var(--color-green)' }}>
              {fmtCcy(Math.abs(remaining))}
            </span>
          </div>
          <div className="flex justify-between text-xs font-mono tabular-nums">
            <span className="text-[var(--color-text-muted)]">Budget</span>
            <span className="text-[var(--color-text-muted)]">{fmtCcy(limit)}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
