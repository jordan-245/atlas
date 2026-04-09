import { fmtCcy } from '../../lib/format'

interface Props {
  name: string
  limit: number
  spent: number
}

function barColor(pct: number, over: boolean): string {
  if (over) return 'var(--color-red)'
  if (pct > 80) return '#f59e0b'
  return 'var(--color-green)'
}

function pctColor(pct: number, over: boolean): string {
  if (over) return 'text-[var(--color-red)]'
  if (pct > 80) return 'text-[#f59e0b]'
  return 'text-[var(--color-text-muted)]'
}

export function BudgetCard({ name, limit, spent }: Props) {
  const pct = Math.min(100, (spent / limit) * 100)
  const remaining = limit - spent
  const over = spent > limit

  return (
    <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <div className="flex items-center justify-between">
        <div className="font-mono font-semibold text-sm truncate">{name}</div>
        <div className={`font-mono text-xs ml-2 shrink-0 ${pctColor(pct, over)}`}>
          {pct.toFixed(0)}%
        </div>
      </div>
      <div className="h-2 bg-[var(--color-surface-alt)] rounded-full mt-2 overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: pct + '%', backgroundColor: barColor(pct, over) }}
        />
      </div>
      <div className="flex justify-between mt-3 text-xs font-mono">
        <div>
          <div className="text-[var(--color-text-muted)] text-[10px] uppercase">SPENT</div>
          <div>{fmtCcy(spent)}</div>
        </div>
        <div className="text-right">
          <div className="text-[var(--color-text-muted)] text-[10px] uppercase">{over ? 'OVER' : 'LEFT'}</div>
          <div className={over ? 'text-[var(--color-red)]' : 'text-[var(--color-green)]'}>
            {fmtCcy(Math.abs(remaining))}
          </div>
        </div>
      </div>
    </div>
  )
}
