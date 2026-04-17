import type { OverlayDecision } from '../../api/types'
import { fmtRelativeTime } from '../../lib/format'
import { EmptyState } from '../shared/EmptyState'

interface Props { decisions: OverlayDecision[] }

const ACTION_ICONS: Record<string, string> = {
  tighten: '🔒',
  widen: '🔓',
  hold: '⏸️',
  skip: '⏭️',
  reduce: '📉',
  increase: '📈',
}

function confidenceColor(c: number): string {
  if (c >= 0.7) return 'var(--color-green)'
  if (c >= 0.4) return '#f59e0b'
  return 'var(--color-red)'
}

function confidenceGradient(c: number): string {
  if (c >= 0.7) return 'linear-gradient(90deg, var(--color-green), #22c55eaa)'
  if (c >= 0.4) return 'linear-gradient(90deg, #f59e0b, #f59e0baa)'
  return 'linear-gradient(90deg, var(--color-red), #ef4444aa)'
}

export function OverlayDecisions({ decisions }: Props) {
  if (decisions.length === 0) return <EmptyState message="No overlay decisions" />

  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold mb-3">
        AI OVERLAY DECISIONS ({decisions.length})
      </div>
      <div className="space-y-3">
        {decisions.slice(0, 10).map((d, i) => {
          const confidence = d.confidence ?? 0
          const action = (d.action ?? d.decision ?? '').toLowerCase()
          const icon = ACTION_ICONS[action] ?? '🤖'
          const color = confidenceColor(confidence)

          return (
            <div
              key={d.id ?? i}
              className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-3 hover:translate-y-[-1px] hover:shadow-md transition-all duration-200"
            >
              <div className="flex items-start gap-3">
                <span className="text-lg shrink-0 mt-0.5">{icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <div className="flex items-center gap-2">
                      {d.symbol && (
                        <span className="font-mono font-bold text-sm">{d.symbol}</span>
                      )}
                      <span
                        className="rounded-full px-2 py-0.5 text-[10px] font-mono uppercase"
                        style={{ backgroundColor: `${color}20`, color }}
                      >
                        {d.action ?? d.decision ?? '\u2014'}
                      </span>
                      {d.strategy && (
                        <span className="text-[10px] text-[var(--color-text-muted)] font-mono">
                          {d.strategy}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-[var(--color-text-muted)] font-mono shrink-0">
                      {fmtRelativeTime(d.timestamp)}
                    </span>
                  </div>

                  {(d.reasoning ?? d.rationale) && (
                    <div className="text-xs text-[var(--color-text-muted)] mb-2 line-clamp-2">
                      {d.reasoning ?? d.rationale}
                    </div>
                  )}

                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-1.5 bg-[var(--color-surface-alt)] rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${(confidence * 100).toFixed(0)}%`,
                          background: confidenceGradient(confidence),
                          transition: 'width 0.5s ease-out',
                        }}
                      />
                    </div>
                    <span className="text-[10px] font-mono tabular-nums shrink-0" style={{ color }}>
                      {(confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
