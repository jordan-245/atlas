import { Badge } from '../shared/Badge'
import type { BadgeVariant } from '../shared/Badge'
import { useResearchCoverage } from '../../api/research-queries'
import { Skeleton } from '../layout/Skeleton'
import { fmtNum } from '../../lib/format'
import type { CoverageCell, CoverageCellStatus } from '../../api/research-types'

// Map status → Badge variant + cell background token
const STATUS_VARIANT: Record<CoverageCellStatus | 'never', BadgeVariant> = {
  fresh:      'success',
  stale:      'warning',
  very_stale: 'danger',
  never:      'neutral',
}

// Cell background tints aligned with Badge variants
const CELL_BG: Record<CoverageCellStatus | 'never', string> = {
  fresh:      'bg-green-500/10',
  stale:      'bg-amber-500/10',
  very_stale: 'bg-red-500/10',
  never:      '',
}

function Cell({ cell }: { cell: CoverageCell | null }) {
  if (!cell) {
    return (
      <td className="px-2 py-2 text-center text-xs border border-[var(--color-border)] text-[var(--color-text-muted)]">
        —
      </td>
    )
  }
  const bg = CELL_BG[cell.status] ?? ''
  return (
    <td
      className={`px-2 py-2 text-center text-xs border border-[var(--color-border)] ${bg}`}
      title={cell.updated_at ? `Updated: ${cell.updated_at}` : undefined}
    >
      <div className="font-mono tabular-nums">{fmtNum(cell.sharpe ?? 0, 2)}</div>
      <div className="text-[10px] text-[var(--color-text-muted)]">
        {cell.age_days != null ? `${cell.age_days.toFixed(0)}d` : ''}
      </div>
    </td>
  )
}

export function CoverageMatrix({ enabled }: { enabled: boolean }) {
  const { data, isLoading, error } = useResearchCoverage(enabled)

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return (
    <div className="text-[var(--color-red)] text-sm p-4">Failed to load coverage matrix</div>
  )
  if (!data) return null

  const { strategies, universes, matrix } = data

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">Research Coverage Matrix</h3>
        <div className="flex items-center gap-2 flex-wrap">
          {(['fresh', 'stale', 'very_stale', 'never'] as const).map((status) => {
            const labels: Record<string, string> = {
              fresh: 'Fresh (<7d)', stale: 'Stale (7-14d)', very_stale: 'Very stale (≥14d)', never: 'Never'
            }
            return (
              <Badge key={status} variant={STATUS_VARIANT[status]} size="xs">
                {labels[status]}
              </Badge>
            )
          })}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr>
              <th className="text-left px-2 py-2 sticky left-0 bg-[var(--color-surface)] text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
                Strategy
              </th>
              {universes.map((u) => (
                <th key={u} className="px-2 py-2 text-center text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
                  {u}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {strategies.map((s) => (
              <tr key={s}>
                <td className="text-left px-2 py-2 font-mono text-[11px] sticky left-0 bg-[var(--color-surface)] text-[var(--color-text-muted)]">
                  {s}
                </td>
                {universes.map((u) => (
                  <Cell key={u} cell={matrix[s]?.[u] ?? null} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
