import { useFinanceData } from '../../api/queries'
import { SectionBoundary } from '../layout/SectionBoundary'
import { FinSummaryStrip } from './FinSummaryStrip'
import { SpendingPaceChart } from './SpendingPaceChart'
import { BankAccountsGrid } from './BankAccountsGrid'
import { SpendingBars } from './SpendingBars'
import { BudgetGrid } from './BudgetGrid'
import { MonthlyComparison } from './MonthlyComparison'
import { RecurringExpenses } from './RecurringExpenses'
import { RecentTransactions } from './RecentTransactions'

// FinanceTabSkeleton — pulse placeholders that mirror the actual layout
// Rendered immediately on tab activation before data arrives.
function FinanceTabSkeleton() {
  return (
    <div className="space-y-4 md:space-y-6" aria-busy="true" aria-label="Loading finance data">
      {/* Summary strip */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5">
        <div className="h-2.5 w-20 bg-[var(--color-surface-alt)] rounded animate-pulse mb-4" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i}>
              <div className="h-2 w-16 bg-[var(--color-surface-alt)] rounded animate-pulse mb-2" />
              <div className="h-7 w-24 bg-[var(--color-surface-alt)] rounded animate-pulse" />
            </div>
          ))}
        </div>
      </div>

      {/* Bank accounts grid — 3 cards */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5">
        <div className="h-2.5 w-20 bg-[var(--color-surface-alt)] rounded animate-pulse mb-4" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 bg-[var(--color-surface-alt)] rounded-lg animate-pulse" />
          ))}
        </div>
      </div>

      {/* Budget grid — 4 budget cards */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5">
        <div className="h-2.5 w-20 bg-[var(--color-surface-alt)] rounded animate-pulse mb-4" />
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-28 bg-[var(--color-surface-alt)] rounded-lg animate-pulse" />
          ))}
        </div>
      </div>

      {/* Spending bars placeholder */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5">
        <div className="h-2.5 w-24 bg-[var(--color-surface-alt)] rounded animate-pulse mb-4" />
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="flex items-center gap-3">
              <div className="h-3 w-24 bg-[var(--color-surface-alt)] rounded animate-pulse" />
              <div
                className="h-4 bg-[var(--color-surface-alt)] rounded animate-pulse"
                style={{ width: `${40 + i * 10}%` }}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function FinanceTab() {
  const finance = useFinanceData(true)
  const { data, isLoading } = finance

  if (isLoading || !data) {
    return <FinanceTabSkeleton />
  }

  return (
    <div className="space-y-4 md:space-y-6 stagger">
      <div className="animate-in">
        <SectionBoundary title="Summary">
          <FinSummaryStrip data={data} />
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <SectionBoundary title="Spending Pace">
          {data.insights?.pace_data && data.insights.pace_data.length > 0
            ? <SpendingPaceChart
                paceData={data.insights.pace_data}
                paceStatus={data.insights.pace_status}
                paceDiff={data.insights.pace_diff}
              />
            : null}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <SectionBoundary title="Accounts">
          {data.accounts && data.accounts.length > 0
            ? <BankAccountsGrid accounts={data.accounts} />
            : null}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <SectionBoundary title="Categories">
          {data.monthly_spending?.by_parent_category && data.monthly_spending.by_parent_category.length > 0
            ? <SpendingBars
                categories={data.monthly_spending.by_parent_category}
                total={data.monthly_spending.total}
              />
            : null}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <SectionBoundary title="Budgets">
          {data.insights?.account_limits && Object.keys(data.insights.account_limits).length > 0
            ? <BudgetGrid accountLimits={data.insights.account_limits} accounts={data.accounts ?? []} />
            : null}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <SectionBoundary title="Monthly">
          {data.insights?.monthly_comparison && data.insights.monthly_comparison.length > 0
            ? <MonthlyComparison rows={data.insights.monthly_comparison} />
            : null}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <SectionBoundary title="Recurring">
          {data.insights?.recurring && data.insights.recurring.length > 0
            ? <RecurringExpenses items={data.insights.recurring} />
            : null}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <SectionBoundary title="Transactions">
          {data.recent_transactions && data.recent_transactions.length > 0
            ? <RecentTransactions transactions={data.recent_transactions} />
            : null}
        </SectionBoundary>
      </div>
    </div>
  )
}
