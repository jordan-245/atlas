import type { FinanceData } from '../../api/types'
import { StatCard } from '../shared/StatCard'
import { fmtAud, fmtAudSigned, fmtPct, fmtNum, pnlClass } from '../../lib/format'

interface Props { data: FinanceData }

export function FinSummaryStrip({ data }: Props) {
  const savings = data.performance?.savings_aud ?? 0
  const savingsColor = savings > 0 ? 'var(--color-green)' : savings < 0 ? 'var(--color-red)' : undefined

  return (
    <div data-testid="finance-summary-strip" className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      <StatCard
        label="NET WORTH"
        value={`${fmtAud(data.net_worth?.total_aud)} AUD`}
        sub={`${fmtPct(data.net_worth?.pct_invested)} invested`}
        hero
        subColor="neutral"
      />
      <StatCard
        label="MONTHLY SPEND"
        value={fmtAud(data.performance?.monthly_spending_aud)}
        sub={`Budget: ${fmtAud(data.insights?.total_monthly_budget)}`}
        subColor="neutral"
      />
      <StatCard
        label="SAVINGS THIS MONTH"
        value={<span className={pnlClass(savings)}>{fmtAudSigned(savings)}</span>}
        sub={`Income: ${fmtAud(data.performance?.income_aud)}`}
        accent={savingsColor}
        subColor={savings >= 0 ? 'positive' : 'negative'}
      />
      <StatCard
        label="RUNWAY"
        value={`${fmtNum(data.performance?.runway_months, 1)} mo`}
        sub={`FI Ratio: ${fmtPct(data.performance?.fi_ratio_pct)}`}
        subColor="neutral"
      />
    </div>
  )
}
