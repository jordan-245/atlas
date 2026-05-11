import { useState, useMemo } from 'react'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { ChartGate } from '../shared/ChartGate'
import { EmptyState } from '../shared/EmptyState'
import { Skeleton } from '../layout/Skeleton'
import { ChartTooltip } from '../shared/ChartTooltip'
import { usePnlFilterOptions, usePnlTrades } from '../../api/queries'
import type { PnlFilters } from '../../api/queries'
import { fmtSignedCcy, fmtDateShort, pnlClass } from '../../lib/format'
import { useCssVars } from '../../hooks/useCssVar'
import {
  CHART_GRID,
  CHART_TICK,
  CHART_ANIM,
  CHART_CURSOR,
  SERIES_PORTFOLIO,
} from '../../lib/chart-palette'

// ---------------------------------------------------------------------------
// PnlSlicerRow — horizontal row of 3 dropdowns
// ---------------------------------------------------------------------------
const SELECT_CLASS =
  'text-xs bg-[var(--color-surface-alt)] border border-[var(--color-border)] rounded-lg px-3 py-1.5 text-[var(--color-text)] cursor-pointer'

// Pill-style period/slice selector — matches EquityChart period pills exactly
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
// CumulativePnlBadge — small stat badge showing total filtered P&L
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
// PnlSlicedSection — main export
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
    '--color-text-muted',
  ] as const)

  const portfolioColor = colors['--color-series-portfolio'] || SERIES_PORTFOLIO
  const textMuted = colors['--color-text-muted'] || 'var(--color-text-muted)'

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

  const tickStyle = { ...CHART_TICK, fill: textMuted }

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
      {/* Header row */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <h3 className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
          P&amp;L Breakdown
        </h3>
        {hasData && <CumulativePnlBadge totalPnl={totalPnl} />}
      </div>

      {/* Slicer dropdowns */}
      <div className="mb-4">
        <PnlSlicerRow
          filters={filters}
          markets={opts?.markets ?? []}
          strategies={opts?.strategies ?? []}
          sectors={opts?.sectors ?? []}
          onChange={handleFilterChange}
        />
      </div>

      {/* Chart / skeleton / empty state */}
      {isLoading ? (
        <Skeleton className="h-[220px]" />
      ) : isEmpty ? (
        <EmptyState message="No trades match the current filter" className="h-[220px] flex items-center justify-center" />
      ) : (
        <ChartGate className="h-[220px] md:h-[260px]">
          <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="pnlSlicerGrad" x1="0" y1="0" x2="0" y2="1">
                  {/* opacity 0.30 — matches EquityChart gradient strength */}
                  <stop offset="0%" stopColor={portfolioColor} stopOpacity={0.30} />
                  <stop offset="100%" stopColor={portfolioColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid {...CHART_GRID} />
              <XAxis
                dataKey="date"
                tickFormatter={(v) => fmtDateShort(v as string)}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={40}
                tick={tickStyle}
              />
              <YAxis
                tickFormatter={(v) => fmtSignedCcy(v as number)}
                axisLine={false}
                tickLine={false}
                tick={tickStyle}
                width={80}
              />
              {/* y=0 P&L parity reference line */}
              <ReferenceLine y={0} stroke="var(--color-border)" strokeDasharray="2 2" />
              <Tooltip
                cursor={CHART_CURSOR}
                content={
                  <ChartTooltip
                    labelFormatter={(l) => fmtDateShort(l)}
                    formatter={(v) => fmtSignedCcy(v as number)}
                  />
                }
              />
              <Area
                dataKey="cumPnl"
                name="Cumulative P&L"
                stroke={portfolioColor}
                strokeWidth={2}
                fill="url(#pnlSlicerGrad)"
                connectNulls={true}
                {...CHART_ANIM}
              />
            </AreaChart>
          </ResponsiveContainer>
        </ChartGate>
      )}

      {/* Slice selector pills (when data is loaded) — matches EquityChart period pill style */}
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
