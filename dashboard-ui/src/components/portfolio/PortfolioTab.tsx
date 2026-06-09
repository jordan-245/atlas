import type { ReactNode } from 'react'
import { usePortfolioData, useSystemHealth } from '../../api/queries'
import { Skeleton } from '../layout/Skeleton'
import { SectionBoundary } from '../layout/SectionBoundary'
import { SummaryStrip } from './SummaryStrip'
import { EquityChart } from './EquityChart'
import { PerformanceSection } from './PerformanceSection'
import { PositionsGrid } from './PositionsGrid'
import { OrdersTable } from './OrdersTable'
import { SystemHealth } from './SystemHealth'
import { SectionLabel } from '../ui/kit'

function GroupDivider({ label }: { label: string }) {
  return <SectionLabel>{label}</SectionLabel>
}

function CollapsibleGroup({ label, defaultOpen = false, children }: { label: string; defaultOpen?: boolean; children: ReactNode }) {
  return (
    <details open={defaultOpen} className="group">
      <summary className="flex items-center gap-2 py-2 cursor-pointer list-none select-none px-0.5">
        <span className="w-0.5 h-3.5 rounded-full bg-[var(--color-border)]" />
        <svg className="w-3.5 h-3.5 text-[var(--color-text-muted)] transition-transform duration-200 group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-[11px] uppercase tracking-widest text-[var(--color-text-muted)] font-semibold">{label}</span>
      </summary>
      <div className="space-y-4 md:space-y-6 mt-4">{children}</div>
    </details>
  )
}

export function PortfolioTab() {
  const portfolio = usePortfolioData()
  const health = useSystemHealth()

  return (
    <div className="space-y-4 md:space-y-6 stagger">
      <div className="animate-in">
        <SectionBoundary title="Summary">
          {portfolio.data?.account ? (
            <SummaryStrip
              account={portfolio.data.account}
              todayPnl={portfolio.data.summary?.today_pnl}
              positionsCount={portfolio.data.positions?.length ?? 0}
              asOf={portfolio.data.timestamp}
            />
          ) : (
            <Skeleton className="h-28" />
          )}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <SectionBoundary title="Equity">
          {portfolio.data ? <EquityChart /> : <Skeleton className="h-96" />}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <GroupDivider label="Current State" />
      </div>

      <div className="animate-in">
        <SectionBoundary title="Positions">
          {portfolio.data?.positions ? (
            <PositionsGrid positions={portfolio.data.positions} />
          ) : (
            <Skeleton className="h-48" />
          )}
        </SectionBoundary>
      </div>

      <div className="animate-in">
        <CollapsibleGroup label="Performance" defaultOpen={false}>
          <SectionBoundary title="Performance">
            {portfolio.data ? <PerformanceSection data={portfolio.data} /> : <Skeleton className="h-64" />}
          </SectionBoundary>
          <SectionBoundary title="Orders">
            {portfolio.data?.recent_orders ? <OrdersTable orders={portfolio.data.recent_orders} /> : <Skeleton className="h-32" />}
          </SectionBoundary>
        </CollapsibleGroup>
      </div>

      <div className="animate-in">
        <CollapsibleGroup label="System" defaultOpen={false}>
          <SectionBoundary title="Health">
            {health.data ? <SystemHealth data={health.data} /> : <Skeleton className="h-40" />}
          </SectionBoundary>
        </CollapsibleGroup>
      </div>
    </div>
  )
}
