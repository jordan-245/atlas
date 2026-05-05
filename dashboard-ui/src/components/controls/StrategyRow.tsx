import { useState } from 'react'
import { useChangeStrategyState } from '../../api/admin-queries'
import { ChangeStateModal } from './ChangeStateModal'
import { RevertButton } from './RevertButton'
import { fmtSignedCcy } from '../../lib/format'
import type { StrategyAdminRow } from '../../api/admin-types'

export function StrategyRow({ row }: { row: StrategyAdminRow }) {
  const [open, setOpen] = useState(false)
  const mutation = useChangeStrategyState()
  const overrideExpiringSoon = row.override?.expires_at
    ? (new Date(row.override.expires_at).getTime() - Date.now()) < 7 * 24 * 3600 * 1000
    : false
  const currentState = row.effective_enabled ? 'enabled' : 'disabled'

  async function handleSubmit(req: {
    state: string
    reason: string
    expires_at: string | null | undefined
    confirm_token?: string
    i_understand: boolean
  }) {
    await mutation.mutateAsync({
      market_id: row.market_id,
      strategy: row.strategy,
      body: {
        state: req.state as 'enabled' | 'disabled',
        reason: req.reason,
        expires_at: req.expires_at,
        i_understand: req.i_understand,
      },
    })
  }

  return (
    <div className="border border-[var(--color-border)]/50 rounded-md px-3 py-2 flex items-center justify-between gap-3 text-sm flex-wrap">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-mono min-w-[200px]">{row.strategy}</span>
        <span className={`text-xs ${row.effective_enabled ? 'text-green-400' : 'text-zinc-500'}`}>
          {row.effective_enabled ? '✓ ENABLED' : '— disabled'}
        </span>
        <span className="text-xs text-[var(--color-text-muted)]">w={row.weight.toFixed(2)}</span>
        {row.override && (
          <span
            className={`px-2 py-0.5 rounded text-xs font-mono border ${
              overrideExpiringSoon
                ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                : 'bg-zinc-700/30 text-zinc-300 border-zinc-600/30'
            }`}
            title={`Reason: ${row.override.reason ?? '—'}\nBy: ${row.override.created_by}\nAt: ${row.override.created_at}\nExpires: ${row.override.expires_at ?? 'never'}`}
          >
            override
          </span>
        )}
      </div>
      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
        <span>
          {row.trades_30d} trades · {fmtSignedCcy(row.pnl_30d)} 30d
        </span>
        <span className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-700/30 text-zinc-300 border border-zinc-600/30">
          {row.lifecycle}
        </span>
        {row.override && <RevertButton overrideId={row.override.id} />}
        <button
          onClick={() => setOpen(true)}
          className="px-2 py-0.5 rounded bg-[var(--color-surface-alt)] hover:bg-[var(--color-border)] text-xs"
        >
          Toggle
        </button>
      </div>
      <ChangeStateModal
        open={open}
        onClose={() => setOpen(false)}
        scope="strategy"
        marketId={row.market_id}
        strategyName={row.strategy}
        currentState={currentState}
        currentSource={row.override ? 'override' : 'config'}
        isProduction={false}
        openPositions={row.open_positions}
        trades30d={row.trades_30d}
        pnl30d={row.pnl_30d}
        lifecycle={row.lifecycle}
        onSubmit={handleSubmit}
      />
    </div>
  )
}
