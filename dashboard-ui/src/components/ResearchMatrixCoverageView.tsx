/**
 * ResearchMatrixCoverageView — strategy × universe coverage grid.
 *
 * Visualizes which (strategy, universe) combinations have been sweep-tested
 * in research, their current lifecycle state, and sweep freshness.
 *
 * Cell colour coding:
 *   green  — fresh (< 7d) + Sharpe ≥ 0.3
 *   yellow — stale (7–14d) or borderline Sharpe
 *   red    — very stale (≥ 14d) or Sharpe < 0.2
 *   grey   — RETIRED
 *   null   — no data (empty cell)
 *
 * Spec: Task 20 — Phase 5 Dashboard Research-Matrix Coverage View.
 */

import { useState, useMemo, memo } from 'react'
import { useResearchMatrix } from '../hooks/useResearchMatrix'
import { Badge } from './shared/Badge'
import { Skeleton } from './layout/Skeleton'
import type { MatrixCell, CellHealth, LifecycleState } from '../hooks/useResearchMatrix'
import type { BadgeVariant } from './shared/Badge'

// ── Design constants ──────────────────────────────────────────────────────────

const HEALTH_BG: Record<CellHealth, string> = {
  green:  'bg-green-500/10 hover:bg-green-500/20',
  yellow: 'bg-yellow-500/10 hover:bg-yellow-500/20',
  red:    'bg-red-500/10 hover:bg-red-500/20',
  grey:   'bg-[var(--color-surface-alt)] opacity-50',
}

const HEALTH_BORDER: Record<CellHealth, string> = {
  green:  'border-green-500/30',
  yellow: 'border-yellow-500/30',
  red:    'border-red-500/30',
  grey:   'border-[var(--color-border)]',
}

function lcVariant(state: LifecycleState | null): BadgeVariant {
  if (state === 'RESEARCH') return 'info'
  if (state === 'PAPER') return 'warning'
  if (state === 'LIVE') return 'success'
  return 'neutral'
}

// ── Filter/sort types ─────────────────────────────────────────────────────────

type StateFilter = LifecycleState | 'ALL'
type StalenessFilter = 'ALL' | 'fresh' | 'stale' | 'missing'
type SortKey = 'name' | 'sharpe'

// ── Tooltip ───────────────────────────────────────────────────────────────────

interface TooltipProps {
  cell: MatrixCell
  strategy: string
  universe: string
}

function CellTooltip({ cell, strategy, universe }: TooltipProps) {
  return (
    <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50
                    w-48 rounded-lg bg-[var(--color-surface)] border border-[var(--color-border)]
                    shadow-xl p-3 text-left pointer-events-none">
      <div className="text-[10px] font-mono font-semibold mb-1.5 truncate">
        {strategy} / {universe}
      </div>
      <div className="space-y-1 text-[10px]">
        <div className="flex justify-between gap-3">
          <span className="text-[var(--color-text-muted)]">Sharpe</span>
          <span className="font-mono tabular-nums">
            {cell.sharpe != null ? cell.sharpe.toFixed(2) : '—'}
          </span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-[var(--color-text-muted)]">Trades</span>
          <span className="font-mono tabular-nums">{cell.trades ?? '—'}</span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-[var(--color-text-muted)]">Days stale</span>
          <span className="font-mono tabular-nums">
            {cell.days_stale != null ? `${cell.days_stale}d` : '—'}
          </span>
        </div>
        {cell.lifecycle_state && (
          <div className="flex justify-between gap-3 items-center">
            <span className="text-[var(--color-text-muted)]">State</span>
            <Badge variant={lcVariant(cell.lifecycle_state)} size="xs">
              {cell.lifecycle_state}
            </Badge>
          </div>
        )}
        {cell.in_active_config && (
          <div className="mt-1.5 text-[10px] text-green-400">✓ Active config</div>
        )}
      </div>
    </div>
  )
}

// ── Single cell ───────────────────────────────────────────────────────────────

interface CellProps {
  cell: MatrixCell | null
  strategy: string
  universe: string
}

function MatrixCellView({ cell, strategy, universe }: CellProps) {
  const [hovered, setHovered] = useState(false)

  if (!cell) {
    return (
      <td className="p-1 text-center">
        <div className="w-10 h-10 rounded border border-dashed border-[var(--color-border)]/40
                        flex items-center justify-center">
          <span className="text-[8px] text-[var(--color-text-muted)]/30">—</span>
        </div>
      </td>
    )
  }

  const health = cell.health
  const sharpeStr = cell.sharpe != null ? cell.sharpe.toFixed(1) : '?'

  return (
    <td className="p-1 text-center">
      <div
        className={`relative w-10 h-10 rounded border cursor-default
                    flex flex-col items-center justify-center transition-colors
                    ${HEALTH_BG[health]} ${HEALTH_BORDER[health]}`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        title={`${strategy}/${universe}`}
        data-testid="matrix-cell"
        data-health={health}
      >
        <span className="text-[9px] font-mono tabular-nums leading-none">{sharpeStr}</span>
        {cell.lifecycle_state && (
          <span className="text-[7px] leading-none mt-0.5 opacity-70">
            {cell.lifecycle_state[0]}
          </span>
        )}
        {hovered && (
          <CellTooltip cell={cell} strategy={strategy} universe={universe} />
        )}
      </div>
    </td>
  )
}

// ── Filter/sort controls ──────────────────────────────────────────────────────

interface FilterBarProps {
  stateFilter: StateFilter
  stalenessFilter: StalenessFilter
  sortKey: SortKey
  onStateFilter: (v: StateFilter) => void
  onStalenessFilter: (v: StalenessFilter) => void
  onSortKey: (v: SortKey) => void
}

function FilterBar({
  stateFilter, stalenessFilter, sortKey,
  onStateFilter, onStalenessFilter, onSortKey,
}: FilterBarProps) {
  const selectClass = `text-xs rounded px-2 py-1 bg-[var(--color-surface-alt)]
                       border border-[var(--color-border)] focus:outline-none
                       focus:border-[var(--color-accent)]/60`
  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        Filter:
      </span>
      <select
        value={stateFilter}
        onChange={e => onStateFilter(e.target.value as StateFilter)}
        className={selectClass}
        aria-label="Filter by lifecycle state"
      >
        <option value="ALL">All states</option>
        <option value="RESEARCH">RESEARCH</option>
        <option value="PAPER">PAPER</option>
        <option value="LIVE">LIVE</option>
        <option value="RETIRED">RETIRED</option>
      </select>
      <select
        value={stalenessFilter}
        onChange={e => onStalenessFilter(e.target.value as StalenessFilter)}
        className={selectClass}
        aria-label="Filter by staleness"
      >
        <option value="ALL">Any staleness</option>
        <option value="fresh">Fresh (&lt; 7d)</option>
        <option value="stale">Stale (≥ 7d)</option>
        <option value="missing">No data</option>
      </select>
      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] ml-2">
        Sort:
      </span>
      <select
        value={sortKey}
        onChange={e => onSortKey(e.target.value as SortKey)}
        className={selectClass}
        aria-label="Sort by"
      >
        <option value="name">Name</option>
        <option value="sharpe">Best Sharpe</option>
      </select>
    </div>
  )
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-3 mb-4 text-[10px]">
      {(
        [
          ['green', 'Fresh + Sharpe ≥ 0.3'],
          ['yellow', 'Stale or borderline'],
          ['red', 'Very stale or failing'],
          ['grey', 'Retired'],
        ] as [CellHealth, string][]
      ).map(([health, label]) => (
        <div key={health} className="flex items-center gap-1.5">
          <div className={`w-3 h-3 rounded border ${HEALTH_BG[health]} ${HEALTH_BORDER[health]}`} />
          <span className="text-[var(--color-text-muted)]">{label}</span>
        </div>
      ))}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

function ResearchMatrixCoverageViewInner() {
  const { data, isLoading, error } = useResearchMatrix(true)

  const [stateFilter, setStateFilter] = useState<StateFilter>('ALL')
  const [stalenessFilter, setStalenessFilter] = useState<StalenessFilter>('ALL')
  const [sortKey, setSortKey] = useState<SortKey>('name')

  const universes = data?.universes ?? []

  // Filter + sort the matrix rows
  const filteredRows = useMemo(() => {
    if (!data) return []
    let rows = data.matrix

    // State filter — keep row if ANY cell matches
    if (stateFilter !== 'ALL') {
      rows = rows.filter(r =>
        r.cells.some(c => c?.lifecycle_state === stateFilter),
      )
    }

    // Staleness filter — keep row if ANY cell matches
    if (stalenessFilter !== 'ALL') {
      rows = rows.filter(r =>
        r.cells.some(c => {
          if (c === null) return stalenessFilter === 'missing'
          if (stalenessFilter === 'missing') return c.days_stale === null
          if (stalenessFilter === 'fresh') return (c.days_stale ?? Infinity) < 7
          if (stalenessFilter === 'stale') return (c.days_stale ?? Infinity) >= 7
          return false
        }),
      )
    }

    // Sort
    if (sortKey === 'name') {
      rows = [...rows].sort((a, b) => a.strategy.localeCompare(b.strategy))
    } else {
      rows = [...rows].sort((a, b) => {
        const aMax = Math.max(...a.cells.map(c => c?.sharpe ?? -Infinity))
        const bMax = Math.max(...b.cells.map(c => c?.sharpe ?? -Infinity))
        return bMax - aMax
      })
    }
    return rows
  }, [data, stateFilter, stalenessFilter, sortKey])

  // ── Loading ────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]
                        font-semibold mb-3">
          Research Coverage Matrix
        </div>
        <Skeleton.Chart />
      </div>
    )
  }

  // ── Error ──────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]
                        font-semibold mb-3">
          Research Coverage Matrix
        </div>
        <div className="text-xs text-[var(--color-red)]">
          Failed to load: {(error as Error).message}
        </div>
      </div>
    )
  }

  // ── Empty ──────────────────────────────────────────────────────────────
  if (!data || data.strategies.length === 0) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]
                        font-semibold mb-3">
          Research Coverage Matrix
        </div>
        <p className="text-xs text-[var(--color-text-muted)]">No research data found.</p>
      </div>
    )
  }

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]
                          font-semibold">
            Research Coverage Matrix
          </div>
          <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
            {data.strategies.length} strategies × {universes.length} universes
          </div>
        </div>
        {data.generated_at && (
          <span className="text-[10px] text-[var(--color-text-muted)] tabular-nums">
            Updated {new Date(data.generated_at).toLocaleTimeString()}
          </span>
        )}
      </div>

      <Legend />

      <FilterBar
        stateFilter={stateFilter}
        stalenessFilter={stalenessFilter}
        sortKey={sortKey}
        onStateFilter={setStateFilter}
        onStalenessFilter={setStalenessFilter}
        onSortKey={setSortKey}
      />

      {/* Matrix table */}
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-0">
          <thead>
            <tr>
              {/* Strategy label column */}
              <th className="px-3 py-1 text-left text-[10px] uppercase tracking-wider
                              text-[var(--color-text-muted)] font-semibold sticky left-0
                              bg-[var(--color-surface)] min-w-[140px]">
                Strategy
              </th>
              {universes.map(u => (
                <th
                  key={u}
                  className="px-1 py-1 text-[9px] text-[var(--color-text-muted)]
                              font-mono text-center max-w-[48px] truncate"
                  title={u}
                >
                  {u.length > 8 ? u.slice(0, 8) + '…' : u}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 ? (
              <tr>
                <td
                  colSpan={universes.length + 1}
                  className="px-3 py-4 text-xs text-[var(--color-text-muted)] text-center"
                >
                  No rows match the current filters.
                </td>
              </tr>
            ) : (
              filteredRows.map(matrixRow => (
                <tr key={matrixRow.strategy}>
                  <td className="px-3 py-1 text-[11px] font-mono sticky left-0
                                  bg-[var(--color-surface)] truncate max-w-[140px]"
                      title={matrixRow.strategy}>
                    {matrixRow.strategy}
                  </td>
                  {matrixRow.cells.map((cell, i) => (
                    <MatrixCellView
                      key={universes[i]}
                      cell={cell}
                      strategy={matrixRow.strategy}
                      universe={universes[i]}
                    />
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export const ResearchMatrixCoverageView = memo(ResearchMatrixCoverageViewInner)
