/**
 * HistoricalOverspend.tsx -- 6-month bar chart of spend vs the constant
 * monthly budget. Bars over budget render red, bars under budget green.
 * A dashed grey reference line marks the budget itself.
 *
 * Companion to BurnDownMountain (Variant B4). Lives below the hero chart
 * to expose the overspend HABIT rather than just this month's pace.
 */
import { useMemo } from 'react'
import type { ChartData, ChartOptions } from 'chart.js'
import { Chart } from '../shared/Chart'
import type { MonthlyComparison } from '../../api/types'
import { buildHistoricalRows, fmtCcyShort, fmtSignedCcyShort } from './_burndown-math'

interface HistoricalOverspendProps {
  monthlyComparison: MonthlyComparison[]
  totalMonthlyBudget: number
}

const MONTH_NAMES_SHORT = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const

/**
 * Parse a `YYYY-MM` or ISO date string into a short month label.
 * Falls back to the raw string if unparseable.
 */
function shortMonthLabel(raw: string): string {
  // Accept "2026-05", "2026-05-01", or any ISO-ish string.
  const match = /^(\d{4})-(\d{2})/.exec(raw)
  if (match) {
    const m = Number(match[2])
    if (m >= 1 && m <= 12) return MONTH_NAMES_SHORT[m - 1]
  }
  const d = new Date(raw)
  if (!Number.isNaN(d.getTime())) {
    return MONTH_NAMES_SHORT[d.getUTCMonth()] ?? raw
  }
  return raw
}

// Colour tokens. Kept here rather than reading from CSS vars because Chart.js
// needs concrete strings at render time (the canvas can't resolve var(--...)).
const RED_OVER = '#ef4444'
const GREEN_UNDER = 'rgba(34, 197, 94, 0.7)'
const GREY_BUDGET = '#6b7280'

export function HistoricalOverspend({
  monthlyComparison,
  totalMonthlyBudget,
}: HistoricalOverspendProps) {
  const rows = useMemo(
    () => buildHistoricalRows(monthlyComparison, totalMonthlyBudget),
    [monthlyComparison, totalMonthlyBudget],
  )

  // buildHistoricalRows returns newest-first; reverse so x-axis reads
  // oldest -> newest left to right.
  const oldestFirst = useMemo(() => [...rows].reverse(), [rows])

  const overCount = useMemo(() => rows.filter((r) => r.over).length, [rows])

  const chartData = useMemo<ChartData<'bar' | 'line'>>(() => {
    const labels = oldestFirst.map((r) => shortMonthLabel(r.month))
    const spendingValues = oldestFirst.map((r) => r.spending)
    const barColors = oldestFirst.map((r) => (r.over ? RED_OVER : GREEN_UNDER))

    return {
      labels,
      datasets: [
        {
          type: 'bar' as const,
          label: 'Spending',
          data: spendingValues,
          backgroundColor: barColors,
          borderColor: barColors,
          borderWidth: 1,
          borderRadius: 4,
          maxBarThickness: 48,
        },
        {
          type: 'line' as const,
          label: 'Budget',
          data: oldestFirst.map(() => totalMonthlyBudget),
          borderColor: GREY_BUDGET,
          borderWidth: 1.25,
          borderDash: [4, 4],
          fill: false,
          pointRadius: 0,
          pointHoverRadius: 0,
          tension: 0,
        },
      ],
    } as ChartData<'bar' | 'line'>
  }, [oldestFirst, totalMonthlyBudget])

  const chartOptions = useMemo<ChartOptions<'bar' | 'line'>>(() => ({
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          title: (items) => items[0]?.label ?? '',
          label: (ctx) => {
            const idx = ctx.dataIndex
            const row = oldestFirst[idx]
            if (!row) return ''
            if (ctx.dataset.label === 'Budget') {
              return `Budget: ${fmtCcyShort(row.budget)}`
            }
            // The diff sign mirrors `over` semantics: positive = over budget.
            const diffLine = `${row.over ? 'Over' : 'Under'}: ${fmtSignedCcyShort(row.diff)}`
            return [`Spending: ${fmtCcyShort(row.spending)}`, diffLine]
          },
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: {
          color: 'var(--color-text-muted)',
          font: { size: 10 },
        },
      },
      y: {
        beginAtZero: true,
        ticks: {
          color: 'var(--color-text-muted)',
          font: { size: 10 },
          callback(v) {
            return '$' + Math.round(Number(v)).toLocaleString('en-US')
          },
        },
      },
    },
    animation: { duration: 500, easing: 'easeOutQuart' },
  }), [oldestFirst])

  // ---------- empty state ----------
  const isEmpty = rows.length === 0 || totalMonthlyBudget <= 0

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold">
          6-MONTH HISTORY
        </div>
        {!isEmpty ? (
          <div className="text-[11px] text-[var(--color-text-muted)] font-mono tabular-nums">
            {overCount} of {rows.length} months over budget
          </div>
        ) : null}
      </div>

      {isEmpty ? (
        <div
          className="flex items-center justify-center text-[12px] text-[var(--color-text-muted)]"
          style={{ height: 180 }}
        >
          Not enough history yet.
        </div>
      ) : (
        <Chart
          kind="bar"
          data={chartData as ChartData<'line' | 'bar' | 'doughnut'>}
          options={chartOptions as ChartOptions<'line' | 'bar' | 'doughnut'>}
          height={180}
        />
      )}
    </div>
  )
}
