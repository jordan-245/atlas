import { memo } from 'react'
import type { MacroDimension } from '../../api/types'
import { Sparkline } from '../shared/Sparkline'

interface Props { dimension: MacroDimension }

// Token-driven score → color mapping (no inline hex)
function scoreToColor(score: number): string {
  if (score > 0.5)  return 'var(--color-green)'
  if (score > 0)    return 'var(--color-amber, #f59e0b)'
  if (score > -0.5) return 'var(--color-spending, #f97316)'
  return 'var(--color-red)'
}

function scoreToRingColor(score: number): string {
  // Same palette but expressed as ring border accent (used in boxShadow)
  if (score > 0.5)  return 'var(--color-green)'
  if (score > 0)    return 'var(--color-amber, #f59e0b)'
  if (score > -0.5) return 'var(--color-spending, #f97316)'
  return 'var(--color-red)'
}

// Token-driven tint for card background
function scoreToBgStyle(score: number): React.CSSProperties {
  if (score > 0.5)  return { background: 'color-mix(in srgb, var(--color-green) 6%, transparent)' }
  if (score > 0)    return { background: 'color-mix(in srgb, #f59e0b 6%, transparent)' }
  if (score > -0.5) return { background: 'color-mix(in srgb, #f97316 6%, transparent)' }
  return { background: 'color-mix(in srgb, var(--color-red) 6%, transparent)' }
}

function GaugeCardInner({ dimension }: Props) {
  const score = dimension.score ?? 0
  const weight = dimension.weight ?? 0
  const positive = score >= 0

  const fillColor = scoreToColor(score)
  const ringColor = scoreToRingColor(score)
  const bgStyle = scoreToBgStyle(score)

  const widthPct = Math.min(Math.abs(score) * 50, 50)
  const scoreColor = positive ? 'text-[var(--color-green)]' : 'text-[var(--color-red)]'
  const signedScore = (score >= 0 ? '+' : '') + score.toFixed(3)

  // Signal icon based on score
  const signalIcon = score > 0.5 ? '▲' : score > 0 ? '△' : score > -0.5 ? '▽' : '▼'

  return (
    <div
      data-testid="macro-gauge"
      className="bg-[var(--color-surface-alt)] border border-[var(--color-border)] rounded-lg p-4 transition-colors hover:border-[color-mix(in_srgb,var(--color-border),var(--color-text)_20%)]"
      style={bgStyle}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <span style={{ color: fillColor }} className="text-xs">{signalIcon}</span>
          <div className="text-[10px] uppercase tracking-wider font-medium">{dimension.label ?? dimension.name ?? '\u2014'}</div>
        </div>
        <div className="text-[10px] text-[var(--color-text-muted)] font-mono tabular-nums bg-[var(--color-surface)] rounded px-1.5 py-0.5">
          {(weight * 100).toFixed(0)}%
        </div>
      </div>

      {/* Raw value */}
      <div className="text-xs text-[var(--color-text-muted)] text-right font-mono tabular-nums mb-2">
        {dimension.raw_value ?? ''}
      </div>

      {/* Visual gauge bar — subtle ring accent matching score zone */}
      <div
        className="h-1.5 bg-[var(--color-border)] rounded-full relative mb-2 overflow-hidden"
        style={{ boxShadow: `0 0 0 1px color-mix(in srgb, ${ringColor} 20%, transparent)` }}
      >
        <div className="absolute top-0 bottom-0 left-1/2 w-px bg-[var(--color-text-muted)]/30" />
        <div
          className="absolute top-0 bottom-0 rounded-full transition-all duration-700 ease-out"
          style={{
            left: positive ? '50%' : `${50 - widthPct}%`,
            width: `${widthPct}%`,
            backgroundColor: fillColor,
            boxShadow: `0 0 6px color-mix(in srgb, ${fillColor} 40%, transparent)`,
          }}
        />
      </div>

      {/* Score value — tabular-nums prevents layout jitter */}
      <div className={`font-mono tabular-nums text-sm font-semibold ${scoreColor}`}>{signedScore}</div>

      {dimension.sparkline && dimension.sparkline.length > 0 ? (
        <div className="mt-2">
          <Sparkline data={dimension.sparkline} height={24} />
        </div>
      ) : null}
    </div>
  )
}

export const GaugeCard = memo(GaugeCardInner)
