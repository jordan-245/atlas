import { useId, useState, type KeyboardEvent } from 'react'
import type { FinanceAccount } from '../../api/types'
import { fmtCcy } from '../../lib/format'
import { useSaverTargets, setSaverTarget } from '../../hooks/useSaverTargets'

interface SaverPotsProps {
  accounts: FinanceAccount[]
  avgMonthlyNet?: number
}

const HUES = ['#22c55e', '#3b82f6', '#f59e0b', '#ec4899', '#a855f7', '#14b8a6'] as const

function isSaver(account: FinanceAccount): boolean {
  const t = (account.type ?? '').toString().toLowerCase()
  return t === 'saver' || t === 'savings'
}

function formatEta(months: number): string {
  if (!Number.isFinite(months) || months < 0) return '—'
  if (months < 1) return '< 1 mo'
  if (months <= 12) return `~${Math.round(months)} mo`
  const yrs = Math.floor(months / 12)
  const mo = Math.round(months - yrs * 12)
  if (mo === 0) return `~${yrs} yr`
  return `~${yrs} yr ${mo} mo`
}

function pctColor(pct: number): string {
  if (pct >= 75) return 'var(--color-green)'
  if (pct >= 50) return 'var(--color-amber, #f59e0b)'
  return 'var(--color-text-muted)'
}

interface PotProps {
  account: FinanceAccount
  target: number | undefined
  hue: string
  avgMonthlyNet?: number
}

function SaverPot({ account, target, hue, avgMonthlyNet }: PotProps) {
  const reactId = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const name = account.name ?? '—'
  const balance = account.balance ?? 0

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<string>('')

  const hasTarget = target != null && target > 0
  const pct = hasTarget ? Math.min(100, (balance / (target as number)) * 100) : 0
  const reached = hasTarget && balance >= (target as number)

  // SVG geometry
  const W = 120
  const H = 150
  const cx = 6
  const cy = 6
  const innerW = W - cx * 2
  const innerH = H - cy * 2
  const inset = 3
  const fillW = innerW - inset * 2
  const fillMaxH = innerH - inset * 2
  const fillRatio = hasTarget ? Math.max(0, Math.min(1, balance / (target as number))) : 0
  const targetH = fillRatio * fillMaxH
  const fillY = cy + inset + (fillMaxH - targetH)
  const tickX1 = cx
  const tickX2 = cx + 4
  const tickYs = [
    cy + inset + fillMaxH * 0.25,
    cy + inset + fillMaxH * 0.5,
    cy + inset + fillMaxH * 0.75,
  ]
  const goalY = cy + inset

  // ETA: if balance >= target → null (we show ✓ chip)
  // otherwise: months = (target - balance) / avgMonthlyNet, assuming entire monthly net flows here
  let etaLabel: string | null = null
  if (hasTarget && !reached) {
    if (avgMonthlyNet != null && avgMonthlyNet > 0) {
      const months = ((target as number) - balance) / avgMonthlyNet
      etaLabel = formatEta(months)
    } else {
      etaLabel = '—'
    }
  }

  const clipId = `sp-clip-${reactId}`
  const gradId = `sp-grad-${reactId}`

  function beginEdit() {
    setDraft(hasTarget ? String(target) : '')
    setEditing(true)
  }

  function commit() {
    const trimmed = draft.trim()
    if (trimmed === '') {
      // blank — leave unchanged
      setEditing(false)
      return
    }
    const value = Number(trimmed)
    if (!Number.isFinite(value)) {
      setEditing(false)
      return
    }
    if (value <= 0) {
      setSaverTarget(name, undefined)
      setEditing(false)
      return
    }
    setSaverTarget(name, value)
    setEditing(false)
  }

  function cancel() {
    setEditing(false)
    setDraft('')
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault()
      commit()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancel()
    }
  }

  return (
    <div
      data-testid="saver-pot"
      className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card flex flex-col gap-2"
    >
      {/* Header: name + balance */}
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-sm font-semibold truncate min-w-0" title={name}>
          {name}
        </div>
        <div className="font-mono tabular-nums text-sm font-semibold shrink-0">
          {fmtCcy(balance)}
        </div>
      </div>

      {/* SVG */}
      <div className="flex justify-center py-1">
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${name} pot`}>
          <defs>
            <clipPath id={clipId}>
              <rect
                x={cx + inset}
                y={cy + inset}
                width={fillW}
                height={fillMaxH}
                rx={6}
                ry={6}
              />
            </clipPath>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={hue} stopOpacity={0.95} />
              <stop offset="100%" stopColor={hue} stopOpacity={0.55} />
            </linearGradient>
          </defs>

          {/* Tick marks on left edge at 25/50/75% */}
          <g stroke="var(--color-border)" strokeWidth={1}>
            {tickYs.map((y, i) => (
              <line key={i} x1={tickX1} y1={y} x2={tickX2} y2={y} />
            ))}
          </g>

          {/* Container outline */}
          <rect
            x={cx}
            y={cy}
            width={innerW}
            height={innerH}
            rx={8}
            ry={8}
            fill="var(--color-surface-alt, #0e1115)"
            stroke="var(--color-border)"
            strokeWidth={1.2}
          />

          {/* Liquid (only when target set and balance > 0) */}
          {hasTarget && targetH > 0 && (
            <g clipPath={`url(#${clipId})`}>
              <rect
                x={cx + inset}
                y={fillY}
                width={fillW}
                height={targetH}
                fill={`url(#${gradId})`}
                style={{
                  transition: 'y 0.8s cubic-bezier(0.22,1,0.36,1), height 0.8s cubic-bezier(0.22,1,0.36,1)',
                }}
              />
              {/* Static wave at top of water */}
              <path
                d={`
                  M ${cx - 6} ${fillY}
                  Q ${cx + 8} ${fillY - 4}, ${cx + 22} ${fillY}
                  T ${cx + 50} ${fillY}
                  T ${cx + 78} ${fillY}
                  T ${cx + 106} ${fillY}
                  T ${cx + 134} ${fillY}
                  L ${cx + 134} ${fillY + 8}
                  L ${cx - 6} ${fillY + 8} Z
                `}
                fill={hue}
                opacity={0.85}
              />
            </g>
          )}

          {/* Goal line — dashed, just inside the top */}
          {hasTarget && (
            <line
              x1={cx + inset}
              y1={goalY}
              x2={cx + inset + fillW}
              y2={goalY}
              stroke="var(--color-text-muted)"
              strokeWidth={1}
              strokeDasharray="2 2"
            />
          )}
          {hasTarget && (
            <text
              x={cx + inset + fillW + 2}
              y={goalY + 3}
              fontSize={8}
              fill="var(--color-text-muted)"
              fontFamily="var(--font-mono, ui-monospace)"
            >
              goal
            </text>
          )}

          {/* No-goal state label inside the empty rect */}
          {!hasTarget && (
            <text
              x={W / 2}
              y={H / 2 + 3}
              textAnchor="middle"
              fontSize={9}
              fill="var(--color-text-muted)"
              fontFamily="var(--font-mono, ui-monospace)"
            >
              no goal set
            </text>
          )}
        </svg>
      </div>

      {/* Footer: stats row or inline editor */}
      {editing ? (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-[var(--color-text-muted)] font-mono">$</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            step="any"
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={onKey}
            placeholder="target"
            className="flex-1 min-w-0 bg-transparent border-b border-[var(--color-border)] focus:border-[var(--color-text)] outline-none font-mono tabular-nums text-xs py-0.5 text-[var(--color-text)]"
          />
          <span className="text-[10px] text-[var(--color-text-muted)]">
            enter to save · esc to cancel
          </span>
        </div>
      ) : hasTarget ? (
        <div className="flex items-center justify-between text-xs">
          <span
            className="font-mono tabular-nums font-semibold cursor-pointer hover:opacity-80"
            style={{ color: reached ? 'var(--color-green)' : pctColor(pct) }}
            onClick={beginEdit}
            title="Edit target"
          >
            {pct.toFixed(0)}% to goal
          </span>
          {reached ? (
            <span
              className="font-mono text-[10px] uppercase tracking-wider"
              style={{ color: 'var(--color-green)' }}
            >
              on goal
            </span>
          ) : (
            <span className="font-mono tabular-nums text-[var(--color-text-muted)]">
              {etaLabel}
            </span>
          )}
        </div>
      ) : (
        <button
          type="button"
          onClick={beginEdit}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors text-left font-medium"
        >
          Set goal $
        </button>
      )}
    </div>
  )
}

export function SaverPots({ accounts, avgMonthlyNet }: SaverPotsProps) {
  const targets = useSaverTargets()
  const savers = accounts.filter(isSaver)

  if (savers.length === 0) {
    return (
      <div className="text-sm text-[var(--color-text-muted)]">
        No saver accounts found.
      </div>
    )
  }

  return (
    <div data-testid="saver-pots">
      <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold mb-3">
        SAVERS ({savers.length})
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {savers.map((account, i) => {
          const name = account.name ?? ''
          const entry = targets[name]
          const hue = HUES[i % HUES.length]
          return (
            <SaverPot
              key={name || i}
              account={account}
              target={entry?.target}
              hue={hue}
              avgMonthlyNet={avgMonthlyNet}
            />
          )
        })}
      </div>
    </div>
  )
}
