import { useMemo } from 'react'
import { useFinanceData } from '../../api/queries'
import { Skeleton } from '../layout/Skeleton'
import { SectionBoundary } from '../layout/SectionBoundary'
import { FinSummaryStrip } from './FinSummaryStrip'
import { BurnDownMountain } from './BurnDownMountain'
import { CategoryBurnGrid } from './CategoryBurnGrid'
import { SaverPots } from './SaverPots'
import { WhatIfPanel } from './WhatIfPanel'
import { HistoricalOverspend } from './HistoricalOverspend'
import { RecurringExpenses } from './RecurringExpenses'
import { RecentTransactions } from './RecentTransactions'
import { avgMonthlyNet } from './_burndown-math'

// FinanceTabSkeleton — shimmer placeholders that mirror the actual B4 layout.
function FinanceTabSkeleton() {
  return (
    <div className="space-y-4 md:space-y-6" aria-busy="true" aria-label="Loading finance data">
      {/* Summary strip */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
      </div>
      {/* Burn-down mountain hero */}
      <Skeleton className="h-80 rounded-xl" />
      {/* Category burn grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
      </div>
      {/* Saver pots */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-52 rounded-xl" />)}
      </div>
      {/* What-if + history */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Skeleton className="h-56 rounded-xl" />
        <Skeleton className="h-56 rounded-xl" />
      </div>
    </div>
  )
}

export function FinanceTab() {
  const finance = useFinanceData(true)
  const { data, isLoading } = finance

  // Memoise the average monthly net — used by both SaverPots (for ETAs) and
  // WhatIfPanel (for the cut-and-save projection). Computed once per data
  // change to avoid recomputing on every render.
  const monthlyNet = useMemo(
    () => avgMonthlyNet(data?.insights?.monthly_comparison),
    [data?.insights?.monthly_comparison],
  )

  if (isLoading || !data) {
    return <FinanceTabSkeleton />
  }

  const paceData = data.insights?.pace_data ?? []
  const totalBudget = data.insights?.total_monthly_budget ?? 0
  const hasMountainData = paceData.length > 0 && totalBudget > 0
  const categories = data.monthly_spending?.by_parent_category ?? []
  const trends = data.insights?.category_trends
  const accounts = data.accounts ?? []
  const comparison = data.insights?.monthly_comparison ?? []

  return (
    <div className="space-y-4 md:space-y-6 stagger">
      <div className="animate-in">
        <SectionBoundary title="Summary">
          <FinSummaryStrip data={data} />
        </SectionBoundary>
      </div>

      {hasMountainData && (
        <div className="animate-in">
          <SectionBoundary title="Burn-down">
            <BurnDownMountain
              paceData={paceData}
              totalMonthlyBudget={totalBudget}
              dailyAvg={data.insights?.daily_avg ?? 0}
              daysLeft={data.insights?.days_left}
              projectedTotal={data.insights?.projected_total}
              paceStatus={data.insights?.pace_status}
              paceDiff={data.insights?.pace_diff}
            />
          </SectionBoundary>
        </div>
      )}

      {categories.length > 0 && (
        <div className="animate-in">
          <SectionBoundary title="Categories">
            <CategoryBurnGrid categories={categories} trends={trends} />
          </SectionBoundary>
        </div>
      )}

      {accounts.length > 0 && (
        <div className="animate-in">
          <SectionBoundary title="Savers">
            <SaverPots accounts={accounts} avgMonthlyNet={monthlyNet} />
          </SectionBoundary>
        </div>
      )}

      {(comparison.length > 0 || totalBudget > 0) && (
        <div className="animate-in grid grid-cols-1 lg:grid-cols-2 gap-4">
          <SectionBoundary title="What-if">
            <WhatIfPanel
              accounts={accounts}
              avgMonthlyNet={monthlyNet}
              monthlyComparison={comparison}
            />
          </SectionBoundary>
          <SectionBoundary title="History">
            <HistoricalOverspend
              monthlyComparison={comparison}
              totalMonthlyBudget={totalBudget}
            />
          </SectionBoundary>
        </div>
      )}

      {data.insights?.recurring && data.insights.recurring.length > 0 && (
        <div className="animate-in">
          <SectionBoundary title="Recurring">
            <RecurringExpenses items={data.insights.recurring} />
          </SectionBoundary>
        </div>
      )}

      {data.recent_transactions && data.recent_transactions.length > 0 && (
        <div className="animate-in">
          <SectionBoundary title="Transactions">
            <RecentTransactions transactions={data.recent_transactions} />
          </SectionBoundary>
        </div>
      )}
    </div>
  )
}
