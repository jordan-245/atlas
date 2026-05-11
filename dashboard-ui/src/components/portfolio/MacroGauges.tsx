import { memo } from 'react'
import type { MacroGaugeData } from '../../api/types'
import { GaugeCard } from './GaugeCard'

interface Props { data: MacroGaugeData }

// Token-driven composite color — same tier logic as GaugeCard
function compositeColor(v: number | null | undefined): string {
  if (v == null) return 'var(--color-text-muted)'
  if (v > 0.3)  return 'var(--color-green)'
  if (v > 0)    return 'var(--color-amber, #f59e0b)'
  if (v > -0.3) return 'var(--color-spending, #f97316)'
  return 'var(--color-red)'
}

function MacroGaugesInner({ data }: Props) {
  const composite = data.composite
  const color = compositeColor(composite)
  return (
    <details className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl dash-card" open>
      <summary className="cursor-pointer list-none flex items-center justify-between p-5">
        <h3 className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
          Macro Indicator Gauges
        </h3>
        <div className="flex items-center gap-2">
          {/* Status dot — token color from composite score */}
          <div
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: color }}
          />
          <div className="text-xs font-mono tabular-nums text-[var(--color-text-muted)] bg-[var(--color-surface-alt)] rounded-md px-2 py-1">
            Composite:{' '}
            <span style={{ color }} className="tabular-nums">
              {composite != null ? composite.toFixed(3) : '\u2014'}
            </span>
            {' '}&bull;{' '}
            <span>{data.date ?? ''}</span>
          </div>
        </div>
      </summary>
      <div className="p-5 pt-0 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {(data.dimensions ?? []).map((dim, i) => (
          <GaugeCard key={dim.name ?? i} dimension={dim} />
        ))}
      </div>
    </details>
  )
}

export const MacroGauges = memo(MacroGaugesInner)
