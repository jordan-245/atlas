import { ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { ChartGate } from '../shared/ChartGate'
import type { PacePoint } from '../../api/types'
import { ChartTooltip } from '../shared/ChartTooltip'
import { fmtDateShort, fmtCcy } from '../../lib/format'
import {
  CHART_GRID,
  CHART_TICK,
  CHART_ANIM,
  CHART_CURSOR,
  SERIES_PORTFOLIO,
  SERIES_BENCHMARK,
} from '../../lib/chart-palette'

interface Props {
  paceData: PacePoint[]
  paceStatus?: string
  paceDiff?: number
}

function badgeClass(status: string | undefined): string {
  if (status === 'under') return 'bg-[var(--color-green)]/20 text-[var(--color-green)]'
  if (status === 'over') return 'bg-[var(--color-red)]/20 text-[var(--color-red)]'
  return 'bg-[var(--color-surface-alt)] text-[var(--color-text-muted)]'
}

export function SpendingPaceChart({ paceData, paceStatus, paceDiff }: Props) {
  // Derive budget target from last budget data point
  const budgetTarget = paceData.length > 0
    ? paceData[paceData.length - 1]?.budget
    : undefined

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
      <div className="flex items-center justify-between mb-4">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">SPENDING PACE</div>
        {paceStatus != null ? (
          <div className={`rounded-full px-3 py-1 text-[10px] font-mono tabular-nums font-medium uppercase ${badgeClass(paceStatus)}`}>
            {paceStatus} {paceDiff != null ? fmtCcy(Math.abs(paceDiff)) : null}
          </div>
        ) : null}
      </div>
      <ChartGate className="h-[280px] w-full">
        <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
          <ComposedChart data={paceData}>
            <defs>
              <linearGradient id="spendingGrad" x1="0" y1="0" x2="0" y2="1">
                {/* Use portfolio series token — green in dark, darker green in light */}
                <stop offset="0%" stopColor={SERIES_PORTFOLIO} stopOpacity={0.20} />
                <stop offset="100%" stopColor={SERIES_PORTFOLIO} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid {...CHART_GRID} />
            <XAxis
              dataKey="date"
              tickFormatter={(v) => fmtDateShort(v as string)}
              axisLine={false}
              tickLine={false}
              tick={CHART_TICK}
            />
            <YAxis
              tickFormatter={(v) => '$' + Math.round(v as number).toLocaleString('en-US')}
              axisLine={false}
              tickLine={false}
              tick={CHART_TICK}
            />
            <Tooltip
              cursor={CHART_CURSOR}
              content={
                <ChartTooltip
                  labelFormatter={(l) => fmtDateShort(l)}
                  formatter={(v) => fmtCcy(v)}
                />
              }
            />
            {budgetTarget != null ? (
              <ReferenceLine
                y={budgetTarget}
                stroke="var(--color-text-muted)"
                strokeDasharray="6 4"
                strokeOpacity={0.5}
              />
            ) : null}
            <Area
              dataKey="actual"
              name="Actual"
              stroke={SERIES_PORTFOLIO}
              strokeWidth={2}
              fill="url(#spendingGrad)"
              baseValue={0}
              dot={false}
              {...CHART_ANIM}
              animationDuration={1200}
            />
            <Line
              dataKey="budget"
              name="Budget"
              stroke={SERIES_BENCHMARK}
              strokeDasharray="4 4"
              strokeWidth={1.5}
              dot={false}
              {...CHART_ANIM}
              animationDuration={1200}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartGate>
    </div>
  )
}
