/**
 * Sparkline -- compact inline trend line.
 *
 * External API preserved across the Recharts -> Chart.js migration:
 * callers continue to pass `data: number[]` with optional colour/height/
 * strokeWidth.  Internally renders via the shared <Chart> wrapper.
 *
 * Auto-colour rule kept identical: green when the trend ends >= start,
 * red otherwise.  Callers override with an explicit `color` prop.
 */

import { useMemo } from 'react'
import { Chart } from './Chart'
import type { ChartOptions } from 'chart.js'

interface SparklineProps {
  data: number[]
  color?: string
  height?: number
  strokeWidth?: number
}

export function Sparkline({ data, color, height = 32, strokeWidth = 1.5 }: SparklineProps) {
  const safeData = data ?? []
  const isEmpty = safeData.length === 0

  const resolvedColor =
    color ??
    (!isEmpty && safeData[safeData.length - 1] >= safeData[0] ? '#22c55e' : '#ef4444')

  const chartData = useMemo(
    () => ({
      labels: safeData.map((_, i) => String(i)),
      datasets: [
        {
          label: 'value',
          data: [...safeData],
          borderColor: resolvedColor,
          borderWidth: strokeWidth,
          pointRadius: 0,
          pointHoverRadius: 3,
          fill: false,
          tension: 0.3,
        },
      ],
    }),
    // NB: safeData identity changes whenever the prop changes, which is what we want.
    [safeData, resolvedColor, strokeWidth],
  )

  const options: ChartOptions<'line'> = useMemo(
    () => ({
      animation: { duration: 600, easing: 'easeOutQuart' },
      plugins: {
        tooltip: {
          callbacks: {
            title: () => '',
            label: (ctx) =>
              typeof ctx.parsed.y === 'number'
                ? ctx.parsed.y.toLocaleString()
                : String(ctx.parsed.y),
          },
        },
      },
      layout: { padding: 0 },
    }),
    [],
  )

  if (isEmpty) {
    return <div style={{ height }} />
  }

  return (
    <Chart
      kind="sparkline"
      data={chartData}
      options={options as ChartOptions<'line' | 'bar' | 'doughnut'>}
      height={height}
    />
  )
}
