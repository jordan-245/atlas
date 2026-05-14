/**
 * LifecycleHistoryModal — shows the full transition history for a single
 * (strategy, universe) combination in a vertical timeline.
 *
 * Fetched lazily on open. Placed at components/ (not controls/) for
 * use by the standalone Controls.tsx component.
 */

import { useEffect } from 'react'
import { useStrategyLifecycleHistory } from '../hooks/useStrategyLifecycle'
import { Badge } from './shared/Badge'
import type { BadgeVariant } from './shared/Badge'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Props {
  strategy: string
  universe: string
  open: boolean
  onClose: () => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function lcVariant(state: string | null): BadgeVariant {
  if (state === 'RESEARCH') return 'info'
  if (state === 'PAPER') return 'warning'
  if (state === 'LIVE') return 'success'
  return 'neutral' // RETIRED + null
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

export function LifecycleHistoryModal({ strategy, universe, open, onClose }: Props) {
  const { data, isLoading, error } = useStrategyLifecycleHistory(strategy, universe, open)

  // ESC to close
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const rows = data?.rows ?? []

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="animate-in bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl
                   shadow-2xl max-w-lg w-full max-h-[80vh] flex flex-col p-6"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-2 mb-4 flex-shrink-0">
          <div>
            <h2 className="text-xl font-semibold">Lifecycle History</h2>
            <div className="text-xs text-[var(--color-text-muted)] mt-0.5 font-mono">
              {strategy} · {universe}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-xl leading-none mt-0.5
                       w-8 h-8 flex items-center justify-center rounded-lg
                       hover:bg-[var(--color-surface-alt)] transition-colors"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Timeline body */}
        <div className="overflow-y-auto flex-1 min-h-0">
          {isLoading && (
            <p className="text-xs text-[var(--color-text-muted)] py-6 text-center">
              Loading history…
            </p>
          )}
          {error && (
            <p className="text-xs text-[var(--color-red)] py-6 text-center">
              Failed to load: {(error as Error).message}
            </p>
          )}
          {!isLoading && !error && rows.length === 0 && (
            <p className="text-xs text-[var(--color-text-muted)] py-6 text-center">
              No history found.
            </p>
          )}
          {rows.length > 0 && (
            <ol className="relative border-l border-[var(--color-border)] ml-3 space-y-4">
              {rows.map((row, i) => (
                <li key={i} className="ml-6">
                  <span className="absolute -left-1.5 w-3 h-3 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-alt)]" />
                  <div className="flex items-center gap-2 flex-wrap">
                    {row.from_state ? (
                      <>
                        <Badge variant={lcVariant(row.from_state)} size="xs">
                          {row.from_state}
                        </Badge>
                        <span className="text-[10px] text-[var(--color-text-muted)]">→</span>
                      </>
                    ) : null}
                    <Badge variant={lcVariant(row.to_state)} size="xs">
                      {row.to_state}
                    </Badge>
                    <span className="text-[10px] text-[var(--color-text-muted)] tabular-nums ml-auto">
                      {fmtDate(row.transitioned_at)}
                    </span>
                  </div>
                  {(row.reason || row.operator) && (
                    <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                      {row.reason && <span>{row.reason}</span>}
                      {row.operator && (
                        <span className="ml-2 font-mono opacity-60">by {row.operator}</span>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 mt-4 pt-4 border-t border-[var(--color-border)] text-right">
          <button
            onClick={onClose}
            className="text-xs px-3 py-1.5 rounded-lg bg-[var(--color-surface-alt)]
                       hover:bg-[var(--color-border)] transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
