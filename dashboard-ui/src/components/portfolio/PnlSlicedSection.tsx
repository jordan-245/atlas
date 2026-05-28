import { useState, useMemo } from 'react'
import { Chart } from '../shared/Chart'
import { EmptyState } from '../shared/EmptyState'
import { Skeleton } from '../layout/Skeleton'
import { usePnlFilterOptions, usePnlTrades } from '../../api/queries'
import type { PnlFilters } from '../../api/queries'
import { fmtSignedCcy, fmtDateShort, pnlClass } from '../../lib/format'
import { useCssVars } from '../../hooks/useCssVar'
import { gradientFill } from '../../lib/chart-defaults'
import type { ChartData, ChartOptions } from 'chart.js'

// ---------------------------------------------------------------------------
// PnlSlicerRow -- horizontal row of 3 dropdowns (unchanged)
// ---------------------------------------------------------------------------
const SELECT_CLASS =
  'text-xs bg-[var(--color-surface-alt)] border border-[var(--color-border)] rounded-lg px-3 py-1.5 text-[var(--color-text)] cursor-pointer'

const PILL_ACTIVE = 'bg-[var(--color-accent)]/15 text-[var(--color-accent)] border-[var(--color-accent)]/30'
const PILL_INACTIVE = 'bg-transparent border-[var(--color-border)]/40 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'

interface SlicerRowProps {
  filters: PnlFilters
  markets: string[]
  strategies: string[]
  sectors: string[]
  onChange: <K extends keyof PnlFilters>(key: K, value: string) => void
}

function PnlSlicerRow({ filters, markets, strategies, sectors, onChange }: SlicerRowProps) {
  return (
    <div className="flex flex-wrap gap-2">
      <select
        value={filters.market_id}
        onChange={(e) => onChange('market_id', e.target.value)}
        className={SELECT_CLASS}
        aria-label="Filter by market"
      >
        <option value="">All Markets</option>
        {markets.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>

      <select
        value={filters.strategy}
        onChange={(e) => onChange('strategy', e.target.value)}
        className={SELECT_CLASS}
        aria-label="Filter by strategy"
      >
        <option value="">All Strategies</option>
        {strategies.map((s) => (
          <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
        ))}
      </select>

      <select
        value={filters.sector}
        onChange={(e) => onChange('sector', e.target.value)}
        className={SELECT_CLASS}
        aria-label="Filter by sector"
      >
        <option value="">All Sectors</option>
        {sectors.map((s) => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>
    </div>
  )
}

// ---------------------------------------------------------------------------
// CumulativePnlBadge -- unchanged
// ---------------------------------------------------------------------------
interface CumulativePnlBadgeProps {
  totalPnl: number
}

function CumulativePnlBadge({ totalPnl }: CumulativePnlBadgeProps) {
  const colorClass = pnlClass(totalPnl)
  return (
    <div
      className={`rounded-md px-2.5 py-1 text-xs font-mono bg-[var(--color-surface-alt)] border border-[var(--color-border)] ${colorClass}`}
    >
      {fmtSignedCcy(totalPnl)}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PnlSlicedSection -- Chart.js port
// ---------------------------------------------------------------------------
export function PnlSlicedSection() {
  const [filters, setFilters] = useState<PnlFilters>({
    market_id: '',
    strategy: '',
    sector: '',
  })

  const filterOptions = usePnlFilterOptions()
  const trades = usePnlTrades(filters)

  const colors = useCssVars([
    '--color-series-portfolio',
    '--color-border',
  ] as const)

  const portfolioColor = colors['--color-series-portfolio'] || '#22c55e'
  const borderColor = colors['--color-border'] || '#2a2f37'

  // Build cumulative P&L series from sorted trades
  const { chartData, totalPnl } = useMemo(() => {
    const rows = trades.data ?? []
    if (rows.length === 0) return { chartData: [], totalPnl: 0 }

    const sorted = [...rows].sort((a, b) =>
      (a.date ?? '') < (b.date ?? '') ? -1 : 1
    )

    let cum = 0
    const points = sorted.map((r) => {
      const pnl = r.pnl ?? r.realized_pnl ?? 0
      cum += pnl
      return { date: r.date ?? '', cumPnl: cum }
    })

    return { chartData: points, totalPnl: cum }
  }, [trades.data])

  function handleFilterChange<K extends keyof PnlFilters>(key: K, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }))
  }

  const opts = filterOptions.data
  const isLoading = trades.isLoading
  const isEmpty = !isLoading && Array.isArray(trades.data) && trades.data.length === 0
  const hasData = !isLoading && !isEmpty && chartData.length > 0

  const chartConfig = useMemo<ChartData<'line'>>(() => ({
    labels: chartData.map((d) => d.date),
    datasets: [
      {
        label: 'Cumulative P&L',
        data: chartData.map((d) => d.cumPnl),
        borderColor: portfolioColor,
        borderWidth: 2,
        fill: true,
        backgroundColor: gradientFill(portfolioColor, 0.30) as unknown as string,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.25,
        spanGaps: true,
      },
      // y=0 reference line drawn as a flat dataset (omit when chartData empty)
      ...(chartData.length > 0 ? [{
        label: '_zero',
        data: chartData.map(() => 0),
        borderColor,
        borderWidth: 1,
        borderDash: [2, 2] as number[],
        fill: false,
        pointRadius: 0,
        pointHoverRadius: 0,
        tension: 0,
      }] : []),
    ],
  }), [chartData, portfolioColor, borderColor])

  const chartOptions = useMemo<ChartOptions<'line'>>(() => ({
    plugins: {
      legend: { display: false },
      tooltip: {
        filter: (item) => (item.dataset.label ?? '').charAt(0) !== '_',
        callbacks: {
          title: (items) => (items[0]?.label ? fmtDateShort(items[0].label) : ''),
          label: (ctx) => {
            const v = typeof ctx.parsed.y === 'number' ? ctx.parsed.y : 0
            return fmtSignedCcy(v)
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
            return fmtSignedCcy(Number(v))
          },
        },
      },
    },
    animation: { duration: 500, easing: 'easeOutQuart' },
  }), [])

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <h3 className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
          P&amp;L Breakdown
        </h3>
        {hasData && <CumulativePnlBadge totalPnl={totalPnl} />}
      </div>

      <div className="mb-4">
        <PnlSlicerRow
          filters={filters}
          markets={opts?.markets ?? []}
          strategies={opts?.strategies ?? []}
          sectors={opts?.sectors ?? []}
          onChange={handleFilterChange}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-[220px]" />
      ) : isEmpty ? (
        <EmptyState message="No trades match the current filter" className="h-[220px] flex items-center justify-center" />
      ) : (
        <Chart
          kind="line"
          data={chartConfig as ChartData<'line' | 'bar' | 'doughnut'>}
          options={chartOptions as ChartOptions<'line' | 'bar' | 'doughnut'>}
          height={260}
        />
      )}

      {hasData && (
        <div className="flex items-center gap-1 mt-3 flex-wrap">
          {opts?.strategies && opts.strategies.length > 1 && opts.strategies.map((s) => (
            <button
              key={s}
              onClick={() => handleFilterChange('strategy', filters.strategy === s ? '' : s)}
              className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-medium tracking-wide transition-colors border ${
                filters.strategy === s ? PILL_ACTIVE : PILL_INACTIVE
              }`}
            >
              {s.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
