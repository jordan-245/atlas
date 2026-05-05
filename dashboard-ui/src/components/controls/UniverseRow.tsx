import { useState } from 'react'
import { useChangeUniverseState } from '../../api/admin-queries'
import { ChangeStateModal } from './ChangeStateModal'
import { RevertButton } from './RevertButton'
import { fmtCcy, fmtRelativeTime } from '../../lib/format'
import type { UniverseAdminRow } from '../../api/admin-types'

const STATE_BADGE: Record<string, string> = {
  live: 'bg-green-500/15 text-green-400 border-green-500/30',
  passive: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
  disabled: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
}
const STATE_LABEL: Record<string, string> = {
  live: '🟢 LIVE',
  passive: '🟡 PASSIVE',
  disabled: '⚫ DISABLED',
}

export function UniverseRow({ row }: { row: UniverseAdminRow }) {
  const [open, setOpen] = useState(false)
  const mutation = useChangeUniverseState()

  // Source pill — expiring within 7 days gets amber treatment
  const overrideExpiringSoon = row.override?.expires_at
    ? (new Date(row.override.expires_at).getTime() - Date.now()) < 7 * 24 * 3600 * 1000
    : false

  const isProduction = row.effective_state === 'live'

  async function handleSubmit(req: {
    state: string
    reason: string
    expires_at: string | null | undefined
    confirm_token?: string
    i_understand: boolean
  }) {
    await mutation.mutateAsync({
      market_id: row.market_id,
      body: {
        state: req.state as 'live' | 'passive' | 'disabled',
        reason: req.reason,
        expires_at: req.expires_at,
        confirm_token: req.confirm_token,
        i_understand: req.i_understand,
      },
    })
  }

  return (
    <div className="border border-[var(--color-border)] rounded-lg p-3 flex items-center justify-between gap-3 flex-wrap">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-mono text-sm font-semibold min-w-[140px]">{row.market_id}</span>
        <span
          className={`px-2 py-0.5 rounded text-xs font-mono border ${STATE_BADGE[row.effective_state] ?? ''}`}
        >
          {STATE_LABEL[row.effective_state] ?? row.effective_state}
        </span>
        {row.override ? (
          <span
            className={`px-2 py-0.5 rounded text-xs font-mono border ${
              overrideExpiringSoon
                ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                : 'bg-zinc-700/30 text-zinc-300 border-zinc-600/30'
            }`}
            title={`Reason: ${row.override.reason ?? '—'}\nBy: ${row.override.created_by}\nAt: ${row.override.created_at}\nExpires: ${row.override.expires_at ?? 'never'}`}
          >
            override{' '}
            {row.override.expires_at
              ? `exp ${row.override.expires_at.slice(0, 10)}`
              : 'permanent'}
          </span>
        ) : (
          <span className="px-2 py-0.5 rounded text-xs font-mono border bg-zinc-700/30 text-zinc-400 border-zinc-600/30">
            config
          </span>
        )}
      </div>
      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
        <span title="Open positions">{row.open_positions} pos</span>
        <span>{row.current_equity != null ? fmtCcy(row.current_equity) : '—'}</span>
        <span title="Last trade">
          {row.last_trade_at ? fmtRelativeTime(row.last_trade_at) : 'no trades'}
        </span>
        {row.override && <RevertButton overrideId={row.override.id} />}
        <button
          onClick={() => setOpen(true)}
          className="px-3 py-1 rounded bg-[var(--color-surface-alt)] hover:bg-[var(--color-border)] text-xs"
        >
          Change ▾
        </button>
      </div>
      <ChangeStateModal
        open={open}
        onClose={() => setOpen(false)}
        scope="universe"
        marketId={row.market_id}
        currentState={row.effective_state}
        currentSource={row.override ? 'override' : 'config'}
        isProduction={isProduction}
        openPositions={row.open_positions}
        onSubmit={handleSubmit}
      />
    </div>
  )
}
