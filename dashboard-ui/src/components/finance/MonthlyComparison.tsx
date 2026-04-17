import type { MonthlyComparison as MonthlyComparisonRow } from '../../api/types'
import { DataTable } from '../shared/DataTable'
import type { Column } from '../shared/DataTable'
import { fmtCcy, fmtSignedCcy, pnlClass } from '../../lib/format'

interface Props { rows: MonthlyComparisonRow[] }

function trendIndicator(current: number | undefined, previous: number | undefined, invert = false) {
  if (current == null || previous == null || previous === 0) return null
  const delta = current - previous
  const pctChange = (delta / Math.abs(previous)) * 100
  if (Math.abs(pctChange) < 0.5) return null
  // For spending, up is bad (invert=true). For income, up is good.
  const isPositive = invert ? delta < 0 : delta > 0
  const arrow = delta > 0 ? '▲' : '▼'
  const cls = isPositive ? 'text-[var(--color-green)]' : 'text-[var(--color-red)]'
  return (
    <span className={`text-[10px] ml-1.5 ${cls}`}>
      {arrow} {Math.abs(pctChange).toFixed(0)}%
    </span>
  )
}

function buildColumns(rows: MonthlyComparisonRow[]): Column<MonthlyComparisonRow>[] {
  return [
    {
      key: 'month',
      label: 'Month',
      render: (r) => <span className="font-mono">{r.month ?? '\u2014'}</span>,
    },
    {
      key: 'income',
      label: 'Income',
      align: 'right',
      className: 'hidden sm:table-cell',
      render: (r) => {
        const idx = rows.indexOf(r)
        const prev = idx < rows.length - 1 ? rows[idx + 1] : undefined
        return (
          <span className="font-mono tabular-nums">
            {fmtCcy(r.income)}
            {trendIndicator(r.income, prev?.income, false)}
          </span>
        )
      },
    },
    {
      key: 'spending',
      label: 'Spending',
      align: 'right',
      render: (r) => {
        const idx = rows.indexOf(r)
        const prev = idx < rows.length - 1 ? rows[idx + 1] : undefined
        return (
          <span className="font-mono tabular-nums">
            {fmtCcy(r.spending)}
            {trendIndicator(r.spending, prev?.spending, true)}
          </span>
        )
      },
    },
    {
      key: 'net',
      label: 'Net',
      align: 'right',
      render: (r) => (
        <span className={`font-mono font-semibold tabular-nums ${pnlClass(r.net)}`}>
          {fmtSignedCcy(r.net)}
        </span>
      ),
    },
    {
      key: 'delta',
      label: 'Δ Spend',
      align: 'right',
      className: 'hidden md:table-cell',
      render: (r) => {
        const idx = rows.indexOf(r)
        const prev = idx < rows.length - 1 ? rows[idx + 1] : undefined
        if (!prev?.spending || !r.spending) return <span className="font-mono text-[var(--color-text-muted)]">{'\u2014'}</span>
        const delta = r.spending - prev.spending
        const cls = delta > 0 ? 'text-[var(--color-red)]' : delta < 0 ? 'text-[var(--color-green)]' : 'text-[var(--color-text-muted)]'
        return <span className={`font-mono tabular-nums ${cls}`}>{fmtSignedCcy(delta)}</span>
      },
    },
  ]
}

export function MonthlyComparison({ rows }: Props) {
  const columns = buildColumns(rows)
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold mb-3">
        MONTHLY COMPARISON
      </div>
      <DataTable columns={columns} data={rows} emptyMessage="No comparison data" />
    </div>
  )
}
