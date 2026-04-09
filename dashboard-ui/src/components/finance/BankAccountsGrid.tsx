import type { FinanceAccount } from '../../api/types'
import { AccountCard } from './AccountCard'

interface Props { accounts: FinanceAccount[] }

export function BankAccountsGrid({ accounts }: Props) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium mb-3">
        BANK ACCOUNTS ({accounts.length})
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
        {accounts.map((a, i) => <AccountCard key={a.name ?? i} account={a} />)}
      </div>
    </div>
  )
}
