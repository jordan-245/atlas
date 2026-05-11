import type { VixTermStructure } from '../../api/types'
import { Badge } from '../shared/Badge'
import type { BadgeVariant } from '../shared/Badge'

interface Props { data: VixTermStructure }

// Regime → Badge variant + label
function regimeVariant(regime?: string): { variant: BadgeVariant; label: string } {
  const r = (regime ?? '').toLowerCase()
  const map: Record<string, { variant: BadgeVariant; label: string }> = {
    strong_contango:       { variant: 'success',  label: 'STRONG CONTANGO' },
    contango:              { variant: 'success',  label: 'CONTANGO' },
    flat:                  { variant: 'neutral',  label: 'FLAT' },
    backwardation:         { variant: 'warning',  label: 'BACKWARDATION' },
    extreme_backwardation: { variant: 'danger',   label: 'EXTREME BKWD' },
  }
  return map[r] ?? { variant: 'neutral', label: regime?.toUpperCase() ?? '\u2014' }
}

// Action → Badge variant + label
function actionVariant(action?: string): { variant: BadgeVariant; label: string } {
  const a = (action ?? '').toUpperCase()
  const map: Record<string, BadgeVariant> = {
    NORMAL:       'neutral',
    WATCH:        'warning',
    REDUCE_GROSS: 'danger',
  }
  return { variant: map[a] ?? 'neutral', label: a || '\u2014' }
}

export function VixTermStructureCard({ data }: Props) {
  // Graceful error state
  if (data.error) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium mb-3">
          VIX Term Structure
        </div>
        <div className="text-xs text-[var(--color-red)] font-mono">{data.error}</div>
      </div>
    )
  }

  const ratio      = data.ratio            != null ? data.ratio.toFixed(4)            : '\u2014'
  const vix        = data.vix              != null ? data.vix.toFixed(2)              : '\u2014'
  const vix3m      = data.vix3m            != null ? data.vix3m.toFixed(2)            : '\u2014'
  const persistence = data.persistence_days != null ? `${data.persistence_days}d`     : '\u2014'
  const mean30d    = data.ratio_30d_mean   != null ? data.ratio_30d_mean.toFixed(4)   : '\u2014'
  const min30d     = data.ratio_30d_min    != null ? data.ratio_30d_min.toFixed(4)    : '\u2014'
  const max30d     = data.ratio_30d_max    != null ? data.ratio_30d_max.toFixed(4)    : '\u2014'
  const asOf       = data.as_of ?? ''

  const regime = regimeVariant(data.regime)
  const action = actionVariant(data.action)

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
      {/* Header row */}
      <div className="flex items-center justify-between mb-3">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
          VIX Term Structure
        </div>
        {asOf && (
          <div className="text-[10px] font-mono tabular-nums text-[var(--color-text-muted)]">{asOf}</div>
        )}
      </div>

      {/* Main ratio + regime badge */}
      <div className="flex items-center gap-3 mb-3">
        <span className="font-mono tabular-nums text-2xl text-[var(--color-text)]">{ratio}</span>
        <Badge variant={regime.variant} size="sm">{regime.label}</Badge>
      </div>

      {/* VIX | VIX3M | Persistence sub-row — tabular-nums for stable widths */}
      <div className="flex items-center gap-4 mb-3 text-xs font-mono tabular-nums text-[var(--color-text-muted)]">
        <span>VIX <span className="text-[var(--color-text)]">{vix}</span></span>
        <span className="text-[var(--color-border)]">|</span>
        <span>VIX3M <span className="text-[var(--color-text)]">{vix3m}</span></span>
        <span className="text-[var(--color-border)]">|</span>
        <span>Persistence: <span className="text-[var(--color-text)]">{persistence}</span></span>
      </div>

      {/* Action badge */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)] font-medium">Action:</span>
        <Badge variant={action.variant} size="sm">{action.label}</Badge>
      </div>

      {/* 30-day range fine print */}
      <div className="text-[10px] font-mono tabular-nums text-[var(--color-text-muted)] bg-[var(--color-surface-alt)] rounded-md px-3 py-1.5">
        30d: {min30d} &mdash; {max30d}{' '}
        <span className="opacity-60">(mean {mean30d})</span>
      </div>
    </div>
  )
}
