import type { FinanceAccount } from '../../api/types'
import { BudgetCard } from './BudgetCard'

interface Props {
  accountLimits: Record<string, number>
  accounts: FinanceAccount[]
}

export function BudgetGrid({ accountLimits, accounts }: Props) {
  const entries = Object.entries(accountLimits)
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium mb-3">
        BUDGETS ({entries.length})
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
        {entries.map(([name, limit]) => {
          const acct = accounts.find(a => a.name === name)
          const spent = Math.abs(acct?.balance ?? 0)
          return <BudgetCard key={name} name={name} limit={limit} spent={spent} />
        })}
      </div>
    </div>
  )
}
