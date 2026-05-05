import { useState } from 'react'
import { useAdminUniverses, useAdminStrategies } from '../../api/admin-queries'
import { SectionBoundary } from '../layout/SectionBoundary'
import { UniverseRow } from './UniverseRow'
import { StrategyRow } from './StrategyRow'
import { RecentChangesPanel } from './RecentChangesPanel'
import type { StrategyAdminRow } from '../../api/admin-types'

function UniversesSection() {
  const { data, isLoading, error } = useAdminUniverses(true)
  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card">
      <h3 className="text-sm font-semibold mb-3">Universes</h3>
      {isLoading && (
        <div className="text-xs text-[var(--color-text-muted)]">Loading…</div>
      )}
      {error && (
        <div className="text-xs text-red-400">Failed: {(error as Error).message}</div>
      )}
      <div className="space-y-2">
        {data?.universes.map((u) => (
          <UniverseRow key={u.market_id} row={u} />
        ))}
      </div>
    </div>
  )
}

function StrategiesSection() {
  const { data, isLoading, error } = useAdminStrategies(true)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  if (isLoading) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 text-xs text-[var(--color-text-muted)]">
        Loading strategies…
      </div>
    )
  }
  if (error) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 text-xs text-red-400">
        Failed: {(error as Error).message}
      </div>
    )
  }

  // Group by universe
  const byUniverse: Record<string, StrategyAdminRow[]> = {}
  for (const s of data?.strategies ?? []) {
    if (!byUniverse[s.market_id]) byUniverse[s.market_id] = []
    byUniverse[s.market_id].push(s)
  }
  const universeKeys = Object.keys(byUniverse).sort()

  // Default-expand the first universe
  const isExpanded = (k: string) => expanded[k] ?? k === universeKeys[0]

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card">
      <h3 className="text-sm font-semibold mb-3">Strategies (grouped by universe)</h3>
      <div className="space-y-3">
        {universeKeys.map((u) => {
          const rows = byUniverse[u]
          const open = isExpanded(u)
          return (
            <div key={u}>
              <button
                onClick={() => setExpanded({ ...expanded, [u]: !open })}
                className="w-full text-left text-xs font-mono font-semibold flex items-center gap-2 py-1 text-[var(--color-text)]"
              >
                <span>{open ? '▼' : '▶'}</span>
                <span>{u}</span>
                <span className="text-[var(--color-text-muted)] font-normal">
                  ({rows.length} strategies)
                </span>
              </button>
              {open && (
                <div className="space-y-1 mt-1 pl-4">
                  {rows.map((s) => (
                    <StrategyRow key={`${s.market_id}.${s.strategy}`} row={s} />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function ControlsTab() {
  return (
    <div className="space-y-4 md:space-y-6">
      <SectionBoundary title="Universes">
        <UniversesSection />
      </SectionBoundary>
      <SectionBoundary title="Strategies">
        <StrategiesSection />
      </SectionBoundary>
      <SectionBoundary title="Recent Changes">
        <RecentChangesPanel />
      </SectionBoundary>
    </div>
  )
}
