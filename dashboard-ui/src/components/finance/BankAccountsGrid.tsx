import type { FinanceAccount } from '../../api/types'
import { AccountCard } from './AccountCard'

interface Props { accounts: FinanceAccount[] }

export function BankAccountsGrid({ accounts }: Props) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold mb-3">
        BANK ACCOUNTS ({accounts.length})
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {accounts.map((a, i) => <AccountCard key={a.name ?? i} account={a} />)}
      </div>
    </div>
  )
}
