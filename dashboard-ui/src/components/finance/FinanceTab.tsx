import { useFinanceData } from '../../api/queries'
import { Skeleton } from '../layout/Skeleton'
import { SectionBoundary } from '../layout/SectionBoundary'
import { FinSummaryStrip } from './FinSummaryStrip'
import { SpendingPaceChart } from './SpendingPaceChart'
import { BankAccountsGrid } from './BankAccountsGrid'
import { SpendingBars } from './SpendingBars'
import { BudgetGrid } from './BudgetGrid'
import { MonthlyComparison } from './MonthlyComparison'
import { RecurringExpenses } from './RecurringExpenses'
import { RecentTransactions } from './RecentTransactions'

// FinanceTabSkeleton — shimmer placeholders that mirror the actual layout
// Uses <Skeleton> component (shimmer animation, not raw animate-pulse).
function FinanceTabSkeleton() {
  return (
    <div className="space-y-4 md:space-y-6" aria-busy="true" aria-label="Loading finance data">
      {/* Summary strip */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
      </div>
      {/* Bank accounts */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {[1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
      </div>
      {/* Budget grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {[1, 2, 3].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
      </div>
      {/* Spending bars */}
      <Skeleton className="h-32 rounded-xl" />
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
