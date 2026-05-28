import type { FinanceData } from '../../api/types'
import { StatCard } from '../shared/StatCard'
import { fmtAud, fmtAudSigned, fmtPct, fmtNum, pnlClass } from '../../lib/format'

interface Props { data: FinanceData }

export function FinSummaryStrip({ data }: Props) {
  const savings = data.performance?.savings_aud ?? 0
  const savingsColor = savings > 0 ? 'var(--color-green)' : savings < 0 ? 'var(--color-red)' : undefined

  // Overspend headline metric: projected month-end spend minus budget.
  // Positive = on pace to be OVER (red). Negative = on pace to be UNDER (green).
  const projected = data.insights?.projected_total
  const budget = data.insights?.total_monthly_budget
  const projDiff =
    typeof projected === 'number' && typeof budget === 'number' ? projected - budget : null
  const projOver = projDiff != null && projDiff > 0
  const projColor = projDiff == null
    ? undefined
    : projOver
      ? 'var(--color-red)'
      : 'var(--color-green)'

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
        label="SAVINGS THIS MONTH"
        value={<span className={pnlClass(savings)}>{fmtAudSigned(savings)}</span>}
        sub={`Income: ${fmtAud(data.performance?.income_aud)}`}
        accent={savingsColor}
        subColor={savings >= 0 ? 'positive' : 'negative'}
      />
      <StatCard
        label="PROJECTED VS BUDGET"
        value={
          projDiff == null
            ? <span className="text-[var(--color-text-muted)]">—</span>
            : <span className={projOver ? 'text-[var(--color-red)]' : 'text-[var(--color-green)]'}>
                {fmtAudSigned(projDiff)}
              </span>
        }
        sub={
          projected != null && budget != null
            ? `${fmtAud(projected)} / ${fmtAud(budget)}`
            : 'No projection yet'
        }
        accent={projColor}
        subColor={projDiff == null ? 'neutral' : projOver ? 'negative' : 'positive'}
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
