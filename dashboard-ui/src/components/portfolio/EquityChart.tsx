import { ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import type { DashboardData } from '../../api/types'
import { ReturnBadge } from './ReturnBadge'
import { fmtCcy, fmtDateShort } from '../../lib/format'

interface Props { data: DashboardData }

interface ChartPoint { date: string; portfolio: number | null; spy: number | null }

function mergeSeries(data: DashboardData): ChartPoint[] {
  const portfolioMap = new Map<string, number>()
  for (const p of data.portfolio_history ?? []) {
    if (p.date && p.equity != null) portfolioMap.set(p.date, p.equity)
  }
  const benchMap = new Map<string, number>()
  for (const p of data.benchmark?.curve ?? []) {
    if (p.date && p.equity != null) benchMap.set(p.date, p.equity)
  }
  const dates = new Set<string>([...portfolioMap.keys(), ...benchMap.keys()])
  return Array.from(dates).sort().map((date) => ({
    date,
    portfolio: portfolioMap.get(date) ?? null,
    spy: benchMap.get(date) ?? null,
  }))
}

interface TooltipPayloadItem {
  dataKey?: string | number
  value?: number
}

interface CustomTooltipProps {
  active?: boolean
  payload?: TooltipPayloadItem[]
  label?: string
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null
  const portfolio = payload.find((p) => p.dataKey === 'portfolio')?.value
  const spy = payload.find((p) => p.dataKey === 'spy')?.value
  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-xs font-mono shadow-lg">
      <div className="text-[var(--color-text-muted)] mb-1">{fmtDateShort(label)}</div>
      {portfolio != null && <div className="text-[#22c55e]">Portfolio {fmtCcy(portfolio)}</div>}
      {spy != null && <div className="text-[#a1a1aa]">SPY {fmtCcy(spy)}</div>}
    </div>
  )
}

export function EquityChart({ data }: Props) {
  const chartData = mergeSeries(data)
  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">Equity Curve</h3>
        <ReturnBadge data={data} />
      </div>
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={chartData}>
          <defs>
            <linearGradient id="portfolioGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22c55e" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#22c55e" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
          <XAxis dataKey="date" tickFormatter={(v) => fmtDateShort(v as string)} axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} />
          <YAxis tickFormatter={(v) => '$' + Math.round(v as number).toLocaleString('en-US')} axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} />
          <Tooltip content={<CustomTooltip />} />
          <Area dataKey="portfolio" stroke="#22c55e" strokeWidth={2} fill="url(#portfolioGrad)" isAnimationActive={false} />
          <Line dataKey="spy" stroke="#a1a1aa" strokeWidth={1.5} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
