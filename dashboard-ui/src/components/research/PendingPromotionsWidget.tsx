import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { post } from '../../api/client'
import { qk } from '../../api/keys'
import { usePendingPromotions } from '../../api/research-queries'
import { fmtNum, fmtRelativeTime } from '../../lib/format'
import type { PendingPromotion } from '../../api/research-types'

function PromotionItem({
  p,
  onAction,
}: {
  p: PendingPromotion
  onAction: (msg: string, isError: boolean) => void
}) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)

  async function act(action: 'approve' | 'reject') {
    setBusy(true)
    try {
      const url = `/api/promotions/${p.pending_id}/${action}`
      await post(url, {})
      onAction(
        `${action === 'approve' ? '✓ Approved' : '✗ Rejected'} ${p.strategy}/${p.market}`,
        false,
      )
      void qc.invalidateQueries({ queryKey: qk.promotions.pending() })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      onAction(`Failed: ${msg}`, true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-[var(--color-border)] rounded-lg p-3 flex items-center justify-between gap-3 flex-wrap">
      <div>
        <div className="font-mono text-sm">
          {p.strategy}{' '}
          <span className="text-[var(--color-text-muted)]">/{p.market}</span>
        </div>
        <div className="text-xs text-[var(--color-text-muted)]">
          Δ Sharpe {p.delta_sharpe >= 0 ? '+' : ''}
          {fmtNum(p.delta_sharpe, 4)} → final {fmtNum(p.final_sharpe, 4)} ·{' '}
          {fmtRelativeTime(p.timestamp)}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          disabled={busy}
          onClick={() => void act('approve')}
          className="px-3 py-1 rounded bg-green-500/15 text-green-400 border border-green-500/30 hover:bg-green-500/25 disabled:opacity-50 text-xs"
        >
          {busy ? '…' : 'Approve'}
        </button>
        <button
          disabled={busy}
          onClick={() => void act('reject')}
          className="px-3 py-1 rounded bg-red-500/15 text-red-400 border border-red-500/30 hover:bg-red-500/25 disabled:opacity-50 text-xs"
        >
          {busy ? '…' : 'Reject'}
        </button>
      </div>
    </div>
  )
}

export function PendingPromotionsWidget() {
  const { data, isLoading } = usePendingPromotions()
  const [toast, setToast] = useState<{ msg: string; isError: boolean } | null>(null)

  if (isLoading) return null

  const pending = data?.pending ?? []

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">
          Pending Promotions
          {pending.length > 0 && (
            <span className="ml-2 px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 text-xs">
              {pending.length}
            </span>
          )}
        </h3>
      </div>
      {pending.length === 0 ? (
        <div className="text-xs text-[var(--color-text-muted)]">No pending promotions</div>
      ) : (
        <div className="space-y-2">
          {pending.map((p) => (
            <PromotionItem
              key={p.pending_id}
              p={p}
              onAction={(msg, isError) => setToast({ msg, isError })}
            />
          ))}
        </div>
      )}
      {toast && (
        <div className={`mt-3 text-xs ${toast.isError ? 'text-red-400' : 'text-green-400'}`}>
          {toast.msg}
        </div>
      )}
    </div>
  )
}
