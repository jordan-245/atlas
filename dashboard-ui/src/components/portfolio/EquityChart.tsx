import { useState, useEffect, useMemo } from 'react'
import { ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { ChartGate } from '../shared/ChartGate'
import { Badge } from '../shared/Badge'
import { useEquityChartData } from '../../api/queries'
import { Skeleton } from '../layout/Skeleton'
import { ChartTooltip } from '../shared/ChartTooltip'
import { fmtCcy, fmtDateShort, fmtSignedPct } from '../../lib/format'
import { useCssVars } from '../../hooks/useCssVar'
import {
  CHART_GRID,
  CHART_TICK,
  CHART_ANIM,
  CHART_CURSOR,
  SERIES_PORTFOLIO,
  SERIES_BENCHMARK,
} from '../../lib/chart-palette'

// Period selector options
const PERIODS = [
  { key: '1W', days: 7 },
  { key: '1M', days: 30 },
  { key: '3M', days: 90 },
  { key: 'ALL', days: Infinity },
] as const

type PeriodKey = (typeof PERIODS)[number]['key']

// ---------------------------------------------------------------------------
// EquityReturnBadge — migrated to <Badge> primitive
// ---------------------------------------------------------------------------
interface EquityReturnBadgeProps {
  portfolioReturnPct: number
  alphaVsSpy: number
}

function EquityReturnBadge({ portfolioReturnPct, alphaVsSpy }: EquityReturnBadgeProps) {
  const variant = portfolioReturnPct >= 0 ? 'success' : 'danger'
  return (
    <Badge variant={variant} size="sm">
      {fmtSignedPct(portfolioReturnPct)}&nbsp;({fmtSignedPct(alphaVsSpy)} vs SPY)
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// PeriodSelector — compact ghost-style pill buttons
// ---------------------------------------------------------------------------
function PeriodSelector({ active, onChange }: { active: PeriodKey; onChange: (k: PeriodKey) => void }) {
  return (
    <div className="flex gap-1">
      {PERIODS.map(({ key }) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-medium tracking-wide transition-colors border ${
            active === key
              ? 'bg-[var(--color-accent)]/15 text-[var(--color-accent)] border-[var(--color-accent)]/30'
              : 'bg-transparent border-[var(--color-border)]/40 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
          }`}
        >
          {key}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// EquityChart
// ---------------------------------------------------------------------------
export function EquityChart() {
  const colors = useCssVars([
    '--color-series-portfolio',
    '--color-series-benchmark',
    '--color-text-muted',
  ] as const)

  // Resolved CSS-var values for SVG attributes (can't use var() inside SVG attrs)
  const portfolioColor = colors['--color-series-portfolio'] || SERIES_PORTFOLIO
  const benchmarkColor = colors['--color-series-benchmark'] || SERIES_BENCHMARK
  const textMuted = colors['--color-text-muted'] || 'var(--color-text-muted)'

  const [isMobile, setIsMobile] = useState(false)
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768)
    check()
    window.addEventListener('resize', check, { passive: true })
    return () => window.removeEventListener('resize', check)
  }, [])

  const [period, setPeriod] = useState<PeriodKey>('ALL')

  const query = useEquityChartData()

  const filteredData = useMemo(() => {
    if (!query.data?.chartData) return []
    const all = query.data.chartData
    const p = PERIODS.find((pp) => pp.key === period)
    if (!p || p.days === Infinity) return all
    return all.slice(-p.days)
  }, [query.data?.chartData, period])

  // Derive start equity from the first visible data point for the baseline reference line
  const startEquity = useMemo(() => {
    const first = filteredData[0]
    return first?.portfolio ?? null
  }, [filteredData])

  if (!query.data) return <Skeleton className="h-96" />

  const tickStyle = { ...CHART_TICK, fontSize: isMobile ? 9 : 10, fill: textMuted }

  const tooltipFormatter = (value: number, name: string) => {
    if (name === 'Portfolio' || name === 'SPY') return fmtCcy(value)
    return value.toLocaleString()
  }

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <h3 className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">Equity Curve</h3>
          <PeriodSelector active={period} onChange={setPeriod} />
        </div>
        <EquityReturnBadge
          portfolioReturnPct={query.data.portfolioReturnPct}
          alphaVsSpy={query.data.alphaVsSpy}
        />
      </div>
      <ChartGate className="h-[280px] md:h-[360px]">
        <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
          <ComposedChart data={filteredData}>
            <defs>
              <linearGradient id="portfolioGrad" x1="0" y1="0" x2="0" y2="1">
                {/* opacity 0.30 for slightly stronger fill vs old 0.25 */}
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
              domain={[
                (dataMin: number) => Math.floor(dataMin * 0.99),
                (dataMax: number) => Math.ceil(dataMax * 1.01),
              ]}
              tickFormatter={(v) => '$' + Math.round(v as number).toLocaleString('en-US')}
              axisLine={false}
              tickLine={false}
              tick={tickStyle}
              width={70}
              allowDataOverflow={false}
            />
            <Tooltip
              cursor={CHART_CURSOR}
              content={
                <ChartTooltip
                  labelFormatter={(l) => fmtDateShort(l)}
                  formatter={tooltipFormatter}
                />
              }
            />
            {/* Baseline reference line at period-start equity — graceful (only when derivable) */}
            {startEquity != null && (
              <ReferenceLine
                y={startEquity}
                stroke="var(--color-border)"
                strokeDasharray="2 2"
                strokeOpacity={0.7}
              />
            )}
            <Area
              dataKey="portfolio"
              name="Portfolio"
              stroke={portfolioColor}
              strokeWidth={2}
              fill="url(#portfolioGrad)"
              baseValue="dataMin"
              connectNulls={true}
              {...CHART_ANIM}
              animationDuration={1200}
            />
            <Line
              dataKey="spy"
              name="SPY"
              stroke={benchmarkColor}
              strokeWidth={1.5}
              strokeDasharray="4 4"
              dot={false}
              connectNulls={true}
              {...CHART_ANIM}
              animationDuration={1200}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartGate>
    </div>
  )
}
