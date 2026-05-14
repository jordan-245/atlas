/**
 * LifecycleTransitionModal — manual transition form.
 *
 * Operator selects a new state, types a reason, and provides their
 * operator name. POSTs to /api/strategy-lifecycle/transition.
 */

import { useState, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { post } from '../api/client'
import { Badge } from './shared/Badge'
import type { BadgeVariant } from './shared/Badge'

// ── Types ─────────────────────────────────────────────────────────────────────

export type LifecycleState = 'RESEARCH' | 'PAPER' | 'LIVE' | 'RETIRED'

interface Props {
  strategy: string
  universe: string
  currentState: LifecycleState | null
  open: boolean
  onClose: () => void
  onSuccess?: () => void
}

interface TransitionRequest {
  strategy: string
  universe: string
  new_state: string
  reason: string
  operator: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const ALL_STATES: LifecycleState[] = ['RESEARCH', 'PAPER', 'LIVE', 'RETIRED']

function lcVariant(state: string): BadgeVariant {
  if (state === 'RESEARCH') return 'info'
  if (state === 'PAPER') return 'warning'
  if (state === 'LIVE') return 'success'
  return 'neutral'
}

// ── Component ─────────────────────────────────────────────────────────────────

export function LifecycleTransitionModal({
  strategy,
  universe,
  currentState,
  open,
  onClose,
  onSuccess,
}: Props) {
  const qc = useQueryClient()
  const [newState, setNewState] = useState<LifecycleState>('RESEARCH')
  const [reason, setReason] = useState('')
  const [operator, setOperator] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset form when modal opens
  useEffect(() => {
    if (open) {
      setNewState('RESEARCH')
      setReason('')
      setOperator('')
      setError(null)
      setBusy(false)
    }
  }, [open])

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

  const canSubmit = newState && reason.trim().length >= 5 && operator.trim().length >= 1 && !busy

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      const body: TransitionRequest = {
        strategy,
        universe,
        new_state: newState,
        reason: reason.trim(),
        operator: operator.trim(),
      }
      await post('/api/strategy-lifecycle/transition', body)
      // Invalidate lifecycle cache
      await qc.invalidateQueries({ queryKey: ['strategy-lifecycle'] })
      onSuccess?.()
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="animate-in bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl
                   shadow-2xl max-w-md w-full p-6"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-2 mb-5">
          <div>
            <h2 className="text-xl font-semibold">Manual Transition</h2>
            <div className="text-xs text-[var(--color-text-muted)] mt-0.5 font-mono">
              {strategy} · {universe}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]
                       w-8 h-8 flex items-center justify-center rounded-lg
                       hover:bg-[var(--color-surface-alt)] transition-colors text-xl"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Current state */}
        {currentState && (
          <div className="mb-4 flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            Current state:
            <Badge variant={lcVariant(currentState)} size="xs">{currentState}</Badge>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* New state dropdown */}
          <div>
            <label className="block text-[11px] font-medium uppercase tracking-wider
                               text-[var(--color-text-muted)] mb-1.5">
              New State
            </label>
            <select
              value={newState}
              onChange={e => setNewState(e.target.value as LifecycleState)}
              disabled={busy}
              className="w-full rounded-md px-3 py-2 bg-[var(--color-surface-alt)]
                         border border-[var(--color-border)] text-sm
                         focus:outline-none focus:border-[var(--color-accent)]
                         focus:ring-2 focus:ring-[var(--color-accent)]/20
                         disabled:opacity-50"
            >
              {ALL_STATES.filter(s => s !== currentState).map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          {/* Reason */}
          <div>
            <label className="block text-[11px] font-medium uppercase tracking-wider
                               text-[var(--color-text-muted)] mb-1.5">
              Reason <span className="normal-case opacity-60">(min 5 chars)</span>
            </label>
            <textarea
              value={reason}
              onChange={e => setReason(e.target.value)}
              disabled={busy}
              rows={3}
              placeholder="Describe why this transition is needed…"
              className="w-full rounded-md px-3 py-2 bg-[var(--color-surface-alt)]
                         border border-[var(--color-border)] text-sm resize-none
                         focus:outline-none focus:border-[var(--color-accent)]
                         focus:ring-2 focus:ring-[var(--color-accent)]/20
                         disabled:opacity-50 placeholder:text-[var(--color-text-muted)]"
            />
          </div>

          {/* Operator */}
          <div>
            <label className="block text-[11px] font-medium uppercase tracking-wider
                               text-[var(--color-text-muted)] mb-1.5">
              Operator (your name)
            </label>
            <input
              type="text"
              value={operator}
              onChange={e => setOperator(e.target.value)}
              disabled={busy}
              placeholder="e.g. alice"
              className="w-full rounded-md px-3 py-2 bg-[var(--color-surface-alt)]
                         border border-[var(--color-border)] text-sm
                         focus:outline-none focus:border-[var(--color-accent)]
                         focus:ring-2 focus:ring-[var(--color-accent)]/20
                         disabled:opacity-50 placeholder:text-[var(--color-text-muted)]"
            />
          </div>

          {/* Error */}
          {error && (
            <div className="rounded-lg bg-[var(--color-red)]/10 border border-[var(--color-red)]/30
                             px-3 py-2 text-xs text-[var(--color-red)]">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-lg bg-[var(--color-surface-alt)]
                         hover:bg-[var(--color-border)] transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="text-xs px-4 py-1.5 rounded-lg font-semibold transition-colors
                         bg-[var(--color-accent)] text-white
                         hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {busy ? 'Transitioning…' : `→ ${newState}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
