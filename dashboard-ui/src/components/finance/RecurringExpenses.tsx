import type { RecurringItem } from '../../api/types'
import { DataTable } from '../shared/DataTable'
import type { Column } from '../shared/DataTable'
import { Badge } from '../shared/Badge'
import type { BadgeVariant } from '../shared/Badge'
import { fmtCcy } from '../../lib/format'

interface Props { items: RecurringItem[] }

// Map recurrence cadence to Badge variant
const FREQ_VARIANT: Record<string, BadgeVariant> = {
  weekly:      'accent',
  fortnightly: 'success',
  monthly:     'warning',
  quarterly:   'info',
  yearly:      'neutral',
}

// Colored dot for merchant column
const FREQ_COLORS: Record<string, string> = {
  weekly:      '#6366f1',
  fortnightly: '#22c55e',
  monthly:     '#f59e0b',
  quarterly:   '#ec4899',
  yearly:      '#14b8a6',
}

function freqDot(freq?: string) {
  const color = FREQ_COLORS[(freq ?? '').toLowerCase()] ?? '#a1a1aa'
  return (
    <span
      className="inline-block rounded-full shrink-0 mr-2"
      style={{ width: 8, height: 8, backgroundColor: color }}
      aria-hidden="true"
    />
  )
}

// Rule: rendering-hoist-jsx — static config outside component
const COLUMNS: Column<RecurringItem>[] = [
  {
    key: 'merchant',
    label: 'Merchant',
    render: (r) => (
      <span className="font-mono flex items-center">
        {freqDot(r.frequency)}
        {r.merchant ?? '—'}
      </span>
    ),
  },
  {
    key: 'frequency',
    label: 'Frequency',
    render: (r) => {
      const variant = FREQ_VARIANT[(r.frequency ?? '').toLowerCase()] ?? 'neutral'
      return (
        <Badge variant={variant} size="xs">
          {(r.frequency ?? '—').toUpperCase()}
        </Badge>
      )
    },
  },
  {
    key: 'avg_amount',
    label: 'Avg Amount',
    align: 'right',
    render: (r) => <span className="font-mono tabular-nums">{fmtCcy(r.avg_amount)}</span>,
  },
  {
    key: 'est_monthly',
    label: 'Est Monthly',
    align: 'right',
    render: (r) => <span className="font-mono tabular-nums">{fmtCcy(r.est_monthly)}</span>,
  },
  {
    key: 'total_90d',
    label: '90d Total',
    align: 'right',
    className: 'hidden sm:table-cell',
    render: (r) => <span className="font-mono tabular-nums">{fmtCcy(r.total_90d)}</span>,
  },
]

export function RecurringExpenses({ items }: Props) {
  const estAnnual = items.reduce((sum, i) => sum + (i.est_monthly ?? 0), 0) * 12

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold">
          RECURRING EXPENSES ({items.length})
        </div>
        <div className="text-xs font-mono text-[var(--color-text-muted)] tabular-nums">
          Est. annual: <span className="text-[var(--color-text)] font-semibold">{fmtCcy(estAnnual)}</span>
        </div>
      </div>
      <DataTable columns={COLUMNS} data={items} density="compact" emptyMessage="No recurring expenses" />
    </div>
  )
}
