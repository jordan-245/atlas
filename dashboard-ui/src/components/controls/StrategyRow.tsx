import { useState } from 'react'
import { useChangeStrategyState } from '../../api/admin-queries'
import { useInvalidateLifecycle } from '../../api/lifecycle'
import { ChangeStateModal } from './ChangeStateModal'
import { LifecycleTransitionModal } from './ChangeStateModal'
import { LifecycleActions } from './LifecycleActions'
import { LifecycleHistoryModal } from './LifecycleHistoryModal'
import { RevertButton } from './RevertButton'
import { fmtNum, fmtSignedCcy } from '../../lib/format'
import type { StrategyAdminRow } from '../../api/admin-types'
import type { LifecycleRow, LifecycleActionType } from '../../api/lifecycle'

// ── Lifecycle badge helpers ─────────────────────────────────────────

const LC_BADGE_CLASS: Record<string, string> = {
  RESEARCH: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  PAPER:    'bg-amber-500/15 text-amber-400 border-amber-500/30',
  LIVE:     'bg-green-500/15 text-green-400 border-green-500/30',
  RETIRED:  'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
}

const LC_BADGE_ICON: Record<string, string> = {
  RESEARCH: '🔵',
  PAPER:    '🟡',
  LIVE:     '🟢',
  RETIRED:  '⚫',
}

// ── Gap color helper ────────────────────────────────────────────────

function gapClass(gap: number | null | undefined): string {
  if (gap == null) return 'text-[var(--color-text-muted)]'
  if (gap > 0.5) return 'text-red-400'
  if (gap > 0.3) return 'text-amber-400'
  return 'text-green-400'
}

function m(v: number | null | undefined, digits = 2): string {
  return v != null ? fmtNum(v, digits) : '—'
}

// ── Per-state inline metrics ────────────────────────────────────────

function LifecycleMetrics({ lr }: { lr: LifecycleRow }) {
  switch (lr.state) {
    case 'RESEARCH':
      return lr.research_sharpe != null ? (
        <span className="text-[10px] text-[var(--color-text-muted)] font-mono">
          Research σ {m(lr.research_sharpe)}
        </span>
      ) : null

    case 'PAPER': {
      const gc = gapClass(lr.gap)
      return (
        <span className="text-[10px] font-mono">
          <span className="text-[var(--color-text-muted)]">Paper σ </span>
          <span className="text-[var(--color-text)]">{m(lr.paper_sharpe)}</span>
          {lr.gap != null && (
            <>
              {' '}
              <span className={`px-1 py-0.5 rounded border text-[9px] ${gc === 'text-red-400' ? 'bg-red-500/10 text-red-400 border-red-500/30' : gc === 'text-amber-400' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' : 'bg-green-500/10 text-green-400 border-green-500/30'}`}>
                gap {m(lr.gap)}
              </span>
            </>
          )}
        </span>
      )
    }

    case 'LIVE':
      return lr.live_sharpe != null ? (
        <span className="text-[10px] text-[var(--color-text-muted)] font-mono">
          Live σ <span className="text-[var(--color-text)]">{m(lr.live_sharpe)}</span>
          {lr.live_trades_count != null && (
            <> · {m(lr.live_trades_count, 0)} trades</>
          )}
        </span>
      ) : null

    default:
      return null
  }
}

// ── Component ───────────────────────────────────────────────────────

interface Props {
  row: StrategyAdminRow
  lifecycleRow?: LifecycleRow
}

export function StrategyRow({ row, lifecycleRow }: Props) {
  // ── Config override modal state ──────────────────────────────────
  const [overrideOpen,    setOverrideOpen]    = useState(false)
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

  // ── Lifecycle modal state ────────────────────────────────────────
  const [historyOpen,    setHistoryOpen]    = useState(false)
  const [activeAction,   setActiveAction]   = useState<LifecycleActionType | null>(null)
  const invalidateLifecycle = useInvalidateLifecycle()

  function handleLifecycleAction(action: LifecycleActionType) {
    setActiveAction(action)
  }

  function closeLifecycleModal() {
    setActiveAction(null)
  }

  return (
    <div className="border border-[var(--color-border)]/50 rounded-md px-3 py-2 flex items-start justify-between gap-3 text-sm flex-wrap">

      {/* ── Left: identity + enable/disable status ── */}
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

      {/* ── Right: metrics + badges + actions ── */}
      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)] flex-wrap">
        {/* Existing 30d stats */}
        <span>
          {row.trades_30d} trades · {fmtSignedCcy(row.pnl_30d)} 30d
        </span>

        {/* Old lifecycle field (ACTIVE/WATCH/RETIRED/UNKNOWN) — keep for backward compat */}
        <span className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-700/30 text-zinc-300 border border-zinc-600/30">
          {row.lifecycle}
        </span>

        {/* NEW: Lifecycle state badge — clickable → history modal */}
        {lifecycleRow && (
          <>
            <button
              onClick={() => setHistoryOpen(true)}
              title="Click to see lifecycle history"
              className={`px-1.5 py-0.5 rounded text-[10px] font-mono border cursor-pointer hover:opacity-80 transition-opacity ${LC_BADGE_CLASS[lifecycleRow.state] ?? ''}`}
              data-testid="lifecycle-state-badge"
            >
              {LC_BADGE_ICON[lifecycleRow.state]} {lifecycleRow.state}
            </button>
            {/* Per-state metrics inline */}
            <LifecycleMetrics lr={lifecycleRow} />
          </>
        )}

        {/* Existing action buttons */}
        {row.override && <RevertButton overrideId={row.override.id} />}
        <button
          onClick={() => setOverrideOpen(true)}
          className="px-2 py-0.5 rounded bg-[var(--color-surface-alt)] hover:bg-[var(--color-border)] text-xs"
        >
          Toggle
        </button>

        {/* NEW: Lifecycle action buttons */}
        {lifecycleRow && (
          <LifecycleActions
            row={lifecycleRow}
            onAction={handleLifecycleAction}
            disabled={false}
          />
        )}
      </div>

      {/* Config override modal */}
      <ChangeStateModal
        open={overrideOpen}
        onClose={() => setOverrideOpen(false)}
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

      {/* Lifecycle history modal */}
      {lifecycleRow && (
        <LifecycleHistoryModal
          strategy={lifecycleRow.strategy}
          universe={lifecycleRow.universe}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
        />
      )}

      {/* Lifecycle transition modal */}
      {lifecycleRow && activeAction && (
        <LifecycleTransitionModal
          open={activeAction !== null}
          onClose={closeLifecycleModal}
          action={activeAction}
          row={lifecycleRow}
          onSuccess={invalidateLifecycle}
        />
      )}
    </div>
  )
}
