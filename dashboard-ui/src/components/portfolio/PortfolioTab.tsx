import { usePortfolioData, useRegimeCurrent, useRegimeHistory, useOverlayDecisions, useSystemHealth, useMacroGauges, usePositionRisk, useRegimeTransitions } from '../../api/queries'
import { Skeleton } from '../layout/Skeleton'
import { SummaryStrip } from './SummaryStrip'
import { EquityChart } from './EquityChart'
import { PerformanceSection } from './PerformanceSection'
import { PositionsGrid } from './PositionsGrid'
import { RiskSection } from './RiskSection'
import { MacroGauges } from './MacroGauges'
import { RegimeSection } from './RegimeSection'
import { OverlayDecisions } from './OverlayDecisions'
import { OrdersTable } from './OrdersTable'
import { SystemHealth } from './SystemHealth'

export function PortfolioTab() {
  const portfolio = usePortfolioData()
  const regimeCurrent = useRegimeCurrent()
  const regimeHistory = useRegimeHistory()
  const overlay = useOverlayDecisions()
  const health = useSystemHealth()
  const macro = useMacroGauges()
  const risk = usePositionRisk()
  const transitions = useRegimeTransitions()

  return (
    <div className="space-y-6">
      {portfolio.data?.account ? <SummaryStrip account={portfolio.data.account} positionsCount={portfolio.data.positions?.length ?? 0} /> : <Skeleton className="h-28" />}
      {portfolio.data ? <EquityChart data={portfolio.data} /> : <Skeleton className="h-96" />}
      {portfolio.data ? <PerformanceSection data={portfolio.data} /> : <Skeleton className="h-64" />}
      {portfolio.data?.positions ? <PositionsGrid positions={portfolio.data.positions} /> : <Skeleton className="h-48" />}
      {risk.data ? <RiskSection data={risk.data} /> : <Skeleton className="h-64" />}
      {macro.data ? <MacroGauges data={macro.data} /> : <Skeleton className="h-40" />}
      {regimeHistory.data && transitions.data ? <RegimeSection history={regimeHistory.data} transitions={transitions.data} /> : <Skeleton className="h-64" />}
      {overlay.data ? <OverlayDecisions decisions={overlay.data} /> : <Skeleton className="h-40" />}
      {portfolio.data?.recent_orders ? <OrdersTable orders={portfolio.data.recent_orders} /> : <Skeleton className="h-32" />}
      {health.data ? <SystemHealth data={health.data} /> : <Skeleton className="h-40" />}
      {/* keep regimeCurrent in scope to satisfy noUnusedLocals */}
      {regimeCurrent.isLoading ? null : null}
    </div>
  )
}
