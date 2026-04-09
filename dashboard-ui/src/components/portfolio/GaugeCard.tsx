import type { MacroDimension } from '../../api/types'
import { Sparkline } from '../shared/Sparkline'

interface Props { dimension: MacroDimension }

export function GaugeCard({ dimension }: Props) {
  const score = dimension.score ?? 0
  const weight = dimension.weight ?? 0
  const positive = score >= 0
  const fillColor = positive ? (score > 0.5 ? '#22c55e' : '#f59e0b') : '#ef4444'
  const widthPct = Math.min(Math.abs(score) * 50, 50)
  const scoreColor = positive ? 'text-[var(--color-green)]' : 'text-[var(--color-red)]'
  const signedScore = (score >= 0 ? '+' : '') + score.toFixed(3)

  return (
    <div className="bg-[var(--color-surface-alt)] border border-[var(--color-border)] rounded-lg p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[10px] uppercase tracking-wider font-medium">{dimension.label ?? dimension.name ?? '\u2014'}</div>
        <div className="text-xs text-[var(--color-text-muted)] font-mono">{(weight * 100).toFixed(0)}%</div>
      </div>
      <div className="text-xs text-[var(--color-text-muted)] text-right font-mono mb-2">{dimension.raw_value ?? ''}</div>
      <div className="h-2 bg-[var(--color-border)] rounded-full relative mb-2">
        <div className="absolute top-0 bottom-0 left-1/2 w-px bg-[var(--color-text-muted)]/50" />
        <div
          className="absolute top-0 bottom-0 rounded-full"
          style={{
            left: positive ? '50%' : `${50 - widthPct}%`,
            width: `${widthPct}%`,
            backgroundColor: fillColor,
          }}
        />
      </div>
      <div className={`font-mono text-sm ${scoreColor}`}>{signedScore}</div>
      {dimension.sparkline && dimension.sparkline.length > 0 && (
        <div className="mt-2">
          <Sparkline data={dimension.sparkline} height={24} />
        </div>
      )}
    </div>
  )
}
