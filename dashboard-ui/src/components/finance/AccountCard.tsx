import type { FinanceAccount } from '../../api/types'
import { fmtCcy } from '../../lib/format'

interface Props { account: FinanceAccount }

function barColor(pct: number): string {
  if (pct > 80) return 'var(--color-red)'
  if (pct > 60) return '#f59e0b'
  return 'var(--color-green)'
}

export function AccountCard({ account }: Props) {
  const balance = account.balance ?? 0
  const limit = account.limit
  const hasLimit = limit != null && limit > 0
  const pct = hasLimit ? Math.min(100, Math.abs(balance) / (limit as number) * 100) : 0

  return (
    <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <div className="flex items-center justify-between">
        <div className="font-mono font-semibold text-base truncate">{account.name ?? '\u2014'}</div>
        {account.type != null ? (
          <div className="rounded-full px-2 py-0.5 text-[10px] font-mono bg-[var(--color-surface-alt)] text-[var(--color-text-muted)] ml-2 shrink-0">
            {account.type}
          </div>
        ) : null}
      </div>
      <div className={`font-mono text-xl font-semibold mt-3 ${balance < 0 ? 'text-[var(--color-red)]' : ''}`}>
        {fmtCcy(account.balance)}
      </div>
      {hasLimit ? (
        <>
          <div className="h-1.5 bg-[var(--color-surface-alt)] rounded-full mt-3">
            <div
              className="h-full rounded-full"
              style={{ width: pct + '%', backgroundColor: barColor(pct) }}
            />
          </div>
          <div className="text-xs text-[var(--color-text-muted)] mt-1 font-mono">
            {fmtCcy(Math.abs(balance))} / {fmtCcy(limit as number)}
          </div>
        </>
      ) : null}
    </div>
  )
}
