import { useState } from 'react'
import { useRegimeCurrent, usePositionRisk, useRuinProbability, useSystemHealth } from '../../api/queries'

interface SourceEntry {
  name: string
  asOf: string
}

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1) return '<1m'
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h`
  return `${Math.floor(hrs / 24)}d`
}

function ageHours(iso: string): number {
  return (Date.now() - new Date(iso).getTime()) / (1000 * 60 * 60)
}

export function DataFreshnessChip() {
  const [open, setOpen] = useState(false)
  const { data: regime } = useRegimeCurrent()
  const { data: risk } = usePositionRisk()
  const { data: ruin } = useRuinProbability()
  const { data: health } = useSystemHealth()

  const sources: SourceEntry[] = []

  if (regime?.as_of) sources.push({ name: 'Regime', asOf: regime.as_of })
  if (risk?.as_of) sources.push({ name: 'Risk', asOf: risk.as_of })
  if (ruin?.as_of) sources.push({ name: 'Ruin', asOf: ruin.as_of })
  // ohlcv_last_date is a date string (YYYY-MM-DD) — treat as midnight UTC
  if (health?.data_freshness?.ohlcv_last_date) {
    sources.push({ name: 'OHLCV', asOf: health.data_freshness.ohlcv_last_date + 'T00:00:00Z' })
  }

  // Nothing to show until at least one source has data
  if (sources.length === 0) return null

  const maxAgeH = Math.max(...sources.map((s) => ageHours(s.asOf)))
  const oldestSource = sources.reduce((a, b) => ageHours(a.asOf) >= ageHours(b.asOf) ? a : b)

  const colorClass =
    maxAgeH < 4
      ? 'bg-green-700/30 text-green-300 border-green-700/40'
      : maxAgeH < 24
      ? 'bg-amber-700/30 text-amber-200 border-amber-700/40'
      : 'bg-red-800/30 text-red-200 border-red-800/40'

  const emoji = maxAgeH < 4 ? '🟢' : maxAgeH < 24 ? '🟡' : '🔴'

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-mono border cursor-pointer select-none ${colorClass}`}
        aria-label="Data freshness"
      >
        {emoji} Data&nbsp;·&nbsp;{relativeTime(oldestSource.asOf)} old
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 z-50 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-3 shadow-xl min-w-[170px]">
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-2 font-semibold">
            Data sources
          </div>
          {sources.map((s) => (
            <div key={s.name} className="flex justify-between gap-4 text-[11px] font-mono py-0.5">
              <span className="text-[var(--color-text-muted)]">{s.name}</span>
              <span className="text-[var(--color-text)] tabular-nums">{relativeTime(s.asOf)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
