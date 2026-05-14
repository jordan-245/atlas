/**
 * Controls — standalone Strategy Lifecycle section component.
 *
 * Shows a table of all (strategy, universe) pairs with their promotion
 * state (RESEARCH / PAPER / LIVE / RETIRED) as a colored badge.
 * Clicking a state badge opens the LifecycleHistoryModal.
 * A "Transition" button per row opens the LifecycleTransitionModal.
 *
 * Designed to be embedded in the Controls tab or any other context.
 * Uses useStrategyLifecycle hook → GET /api/strategy-lifecycle.
 */

import { useState, memo } from 'react'
import { useStrategyLifecycle } from '../hooks/useStrategyLifecycle'
import { LifecycleHistoryModal } from './LifecycleHistoryModal'
import { LifecycleTransitionModal } from './LifecycleTransitionModal'
import { Badge } from './shared/Badge'
import { Skeleton } from './layout/Skeleton'
import type { LifecycleRow } from '../hooks/useStrategyLifecycle'
import type { BadgeVariant } from './shared/Badge'
import type { LifecycleState } from './LifecycleTransitionModal'

// ── Helpers ───────────────────────────────────────────────────────────────────

function lcVariant(state: string): BadgeVariant {
  if (state === 'RESEARCH') return 'info'
  if (state === 'PAPER') return 'warning'
  if (state === 'LIVE') return 'success'
  return 'neutral' // RETIRED
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return iso
  }
}

function daysInState(row: LifecycleRow & { days_in_state?: number | null }): string {
  const days = (row as unknown as { days_in_state?: number | null })['days_in_state']
  if (days == null) return '—'
  return `${days}d`
}

// ── Row component ─────────────────────────────────────────────────────────────

interface RowProps {
  row: LifecycleRow
  onBadgeClick: (row: LifecycleRow) => void
  onTransitionClick: (row: LifecycleRow) => void
}

function LifecycleTableRow({ row, onBadgeClick, onTransitionClick }: RowProps) {
  return (
    <tr className="border-b border-[var(--color-border)] last:border-0
                   hover:bg-[var(--color-surface-alt)]/40 transition-colors">
      <td className="px-3 py-2.5 text-xs font-mono">{row.strategy}</td>
      <td className="px-3 py-2.5 text-xs font-mono text-[var(--color-text-muted)]">
        {row.universe}
      </td>
      <td className="px-3 py-2.5 text-xs">
        <button
          onClick={() => onBadgeClick(row)}
          title="Click to view history"
          className="cursor-pointer hover:opacity-80 transition-opacity"
        >
          <Badge variant={lcVariant(row.state)} size="xs" data-testid="lifecycle-state-badge">
            {row.state}
          </Badge>
        </button>
      </td>
      <td className="px-3 py-2.5 text-xs text-right tabular-nums font-mono
                      text-[var(--color-text-muted)]">
        {daysInState(row)}
      </td>
      <td className="px-3 py-2.5 text-xs tabular-nums text-[var(--color-text-muted)]">
        {fmtDate(row.entered_state_at)}
      </td>
      <td className="px-3 py-2.5 text-xs text-right">
        <button
          onClick={() => onTransitionClick(row)}
          className="text-[10px] px-2 py-1 rounded bg-[var(--color-surface-alt)]
                     border border-[var(--color-border)] hover:border-[var(--color-accent)]/50
                     hover:text-[var(--color-accent)] transition-colors"
          data-testid="transition-btn"
        >
          Transition
        </button>
      </td>
    </tr>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

function ControlsInner() {
  const { data, isLoading, error } = useStrategyLifecycle(true)

  const [historyTarget, setHistoryTarget] = useState<LifecycleRow | null>(null)
  const [transitionTarget, setTransitionTarget] = useState<LifecycleRow | null>(null)

  // ── Loading state ────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]
                        font-semibold mb-3">
          Strategy Lifecycle
        </div>
        <Skeleton.Text lines={5} />
      </div>
    )
  }

  // ── Error state ──────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]
                        font-semibold mb-3">
          Strategy Lifecycle
        </div>
        <div className="text-xs text-[var(--color-red)]">
          Failed to load: {(error as Error).message}
        </div>
      </div>
    )
  }

  const rows = data?.rows ?? []

  return (
    <>
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl
                      overflow-hidden dash-card">
        <div className="px-4 pt-4 pb-2">
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]
                          font-semibold">
            Strategy Lifecycle
          </div>
          <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
            {rows.length} entr{rows.length === 1 ? 'y' : 'ies'}
          </div>
        </div>

        {rows.length === 0 ? (
          <div className="px-4 pb-4 text-xs text-[var(--color-text-muted)]">
            No lifecycle rows found.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left" data-testid="lifecycle-table">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-3 py-2 text-[10px] uppercase tracking-wider
                                  text-[var(--color-text-muted)] font-semibold">
                    Strategy
                  </th>
                  <th className="px-3 py-2 text-[10px] uppercase tracking-wider
                                  text-[var(--color-text-muted)] font-semibold">
                    Universe
                  </th>
                  <th className="px-3 py-2 text-[10px] uppercase tracking-wider
                                  text-[var(--color-text-muted)] font-semibold">
                    State
                  </th>
                  <th className="px-3 py-2 text-[10px] uppercase tracking-wider
                                  text-[var(--color-text-muted)] font-semibold text-right">
                    Days
                  </th>
                  <th className="px-3 py-2 text-[10px] uppercase tracking-wider
                                  text-[var(--color-text-muted)] font-semibold">
                    Entered
                  </th>
                  <th className="px-3 py-2 text-[10px] uppercase tracking-wider
                                  text-[var(--color-text-muted)] font-semibold text-right">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <LifecycleTableRow
                    key={`${row.strategy}.${row.universe}`}
                    row={row}
                    onBadgeClick={setHistoryTarget}
                    onTransitionClick={setTransitionTarget}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modals */}
      {historyTarget && (
        <LifecycleHistoryModal
          strategy={historyTarget.strategy}
          universe={historyTarget.universe}
          open={Boolean(historyTarget)}
          onClose={() => setHistoryTarget(null)}
        />
      )}
      {transitionTarget && (
        <LifecycleTransitionModal
          strategy={transitionTarget.strategy}
          universe={transitionTarget.universe}
          currentState={(transitionTarget.state as LifecycleState) ?? null}
          open={Boolean(transitionTarget)}
          onClose={() => setTransitionTarget(null)}
        />
      )}
    </>
  )
}

export const Controls = memo(ControlsInner)
