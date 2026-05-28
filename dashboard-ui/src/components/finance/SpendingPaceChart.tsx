import { useMemo } from 'react'
import { Chart } from '../shared/Chart'
import type { PacePoint } from '../../api/types'
import { fmtDateShort, fmtCcy } from '../../lib/format'
import { useCssVars } from '../../hooks/useCssVar'
import { gradientFill } from '../../lib/chart-defaults'
import type { ChartData, ChartOptions } from 'chart.js'

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
  const colors = useCssVars([
    '--color-series-portfolio',
    '--color-series-benchmark',
    '--color-text-muted',
  ] as const)

  const portfolioColor = colors['--color-series-portfolio'] || '#22c55e'
  const benchmarkColor = colors['--color-series-benchmark'] || '#a1a1aa'
  const mutedColor    = colors['--color-text-muted']       || '#8b929d'

  const budgetTarget = paceData.length > 0
    ? paceData[paceData.length - 1]?.budget
    : undefined

  const chartConfig = useMemo<ChartData<'line'>>(() => ({
    labels: paceData.map((d) => d.date),
    datasets: [
      {
        label: 'Actual',
        data: paceData.map((d) => d.actual ?? null) as number[],
        borderColor: portfolioColor,
        borderWidth: 2,
        fill: true,
        backgroundColor: gradientFill(portfolioColor, 0.20) as unknown as string,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.25,
        spanGaps: true,
      },
      {
        label: 'Budget',
        data: paceData.map((d) => d.budget ?? null) as number[],
        borderColor: benchmarkColor,
        borderWidth: 1.5,
        borderDash: [4, 4],
        fill: false,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.25,
        spanGaps: true,
      },
      ...(budgetTarget != null ? [{
        label: '_target',
        data: paceData.map(() => budgetTarget) as number[],
        borderColor: mutedColor,
        borderWidth: 1,
        borderDash: [6, 4],
        fill: false,
        pointRadius: 0,
        pointHoverRadius: 0,
        tension: 0,
      }] : []),
    ],
  }), [paceData, portfolioColor, benchmarkColor, mutedColor, budgetTarget])

  const chartOptions = useMemo<ChartOptions<'line'>>(() => ({
    plugins: {
      legend: { display: false },
      tooltip: {
        filter: (item) => (item.dataset.label ?? '').charAt(0) !== '_',
        callbacks: {
          title: (items) => (items[0]?.label ? fmtDateShort(items[0].label) : ''),
          label: (ctx) => {
            const name = ctx.dataset.label ?? ''
            const v = typeof ctx.parsed.y === 'number' ? ctx.parsed.y : 0
            return `${name}: ${fmtCcy(v)}`
          },
        },
      },
    },
    scales: {
      x: {
        ticks: {
          color: 'var(--color-text-muted)',
          font: { size: 10 },
          maxRotation: 0,
          autoSkipPadding: 24,
          callback(value) {
            return fmtDateShort(this.getLabelForValue(Number(value)) as string)
          },
        },
      },
      y: {
        ticks: {
          color: 'var(--color-text-muted)',
          font: { size: 10 },
          callback(v) {
            return '$' + Math.round(Number(v)).toLocaleString('en-US')
          },
        },
      },
    },
    animation: { duration: 600, easing: 'easeOutQuart' },
  }), [])

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
      <Chart
        kind="line"
        data={chartConfig as ChartData<'line' | 'bar' | 'doughnut'>}
        options={chartOptions as ChartOptions<'line' | 'bar' | 'doughnut'>}
        height={280}
      />
    </div>
  )
}
