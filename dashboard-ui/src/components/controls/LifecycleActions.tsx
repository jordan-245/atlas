/**
 * LifecycleActions — pure presentational component.
 * Renders 1–2 action buttons based on the current lifecycle state.
 * Calls onAction with the action type; parent owns modal state.
 */

import type { LifecycleRow, LifecycleActionType } from '../../api/lifecycle'

interface Props {
  row: LifecycleRow
  onAction: (action: LifecycleActionType) => void
  disabled?: boolean
}

const BASE =
  'px-2 py-0.5 rounded text-xs bg-[var(--color-surface-alt)] hover:bg-[var(--color-border)] ' +
  'border border-[var(--color-border)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors'

const DANGER =
  BASE + ' text-red-400 hover:text-red-300'

const WARN =
  BASE + ' text-amber-400 hover:text-amber-300'

export function LifecycleActions({ row, onAction, disabled = false }: Props) {
  switch (row.state) {
    case 'RESEARCH':
      return (
        <button
          disabled={disabled}
          onClick={() => onAction('promote_paper')}
          className={BASE}
          data-testid="action-promote-paper"
        >
          Promote to PAPER ↑
        </button>
      )

    case 'PAPER':
      return (
        <span className="inline-flex items-center gap-1.5 flex-wrap">
          <button
            disabled={disabled}
            onClick={() => onAction('promote_live')}
            className={BASE}
            data-testid="action-promote-live"
          >
            Promote to LIVE ↑
          </button>
          <button
            disabled={disabled}
            onClick={() => onAction('rollback')}
            className={WARN}
            data-testid="action-rollback"
          >
            ↩ Rollback
          </button>
        </span>
      )

    case 'LIVE':
      return (
        <span className="inline-flex items-center gap-1.5 flex-wrap">
          <button
            disabled={disabled}
            onClick={() => onAction('rollback_paper')}
            className={WARN}
            data-testid="action-rollback-paper"
          >
            ↩ Soft rollback
          </button>
          <button
            disabled={disabled}
            onClick={() => onAction('retire')}
            className={DANGER}
            data-testid="action-retire"
          >
            ⏹ Retire
          </button>
        </span>
      )

    case 'RETIRED':
      return (
        <button
          disabled={disabled}
          onClick={() => onAction('revive')}
          className={BASE}
          data-testid="action-revive"
        >
          ↑ Revive
        </button>
      )

    default:
      return null
  }
}
