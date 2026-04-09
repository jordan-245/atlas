import type { RecurringItem } from '../../api/types'
import { DataTable } from '../shared/DataTable'
import type { Column } from '../shared/DataTable'
import { fmtCcy } from '../../lib/format'

interface Props { items: RecurringItem[] }

export function RecurringExpenses({ items }: Props) {
  const columns: Column<RecurringItem>[] = [
    {
      key: 'merchant',
      label: 'Merchant',
      render: (r) => <span className="font-mono">{r.merchant ?? '\u2014'}</span>,
    },
    {
      key: 'frequency',
      label: 'Frequency',
      render: (r) => (
        <span className="rounded-full px-2 py-0.5 text-[10px] font-mono uppercase bg-[var(--color-surface-alt)] text-[var(--color-text-muted)]">
          {r.frequency ?? '\u2014'}
        </span>
      ),
    },
    {
      key: 'avg_amount',
      label: 'Avg Amount',
      align: 'right',
      render: (r) => <span className="font-mono">{fmtCcy(r.avg_amount)}</span>,
    },
    {
      key: 'est_monthly',
      label: 'Est Monthly',
      align: 'right',
      render: (r) => <span className="font-mono">{fmtCcy(r.est_monthly)}</span>,
    },
    {
      key: 'total_90d',
      label: '90d Total',
      align: 'right',
      render: (r) => <span className="font-mono">{fmtCcy(r.total_90d)}</span>,
    },
  ]

  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium mb-3">
        RECURRING EXPENSES ({items.length})
      </div>
      <DataTable columns={columns} data={items} emptyMessage="No recurring expenses" />
    </div>
  )
}
