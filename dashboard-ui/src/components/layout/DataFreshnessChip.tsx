import { useState } from 'react'
import { useRegimeCurrent, usePositionRisk, useRuinProbability, useSystemHealth } from '../../api/queries'

interface SourceEntry {
  name: string
  asOf: string
  label?: string   // extended description shown in the tooltip panel
  fresh?: boolean  // when true, this source is treated as age=0 for colour-band logic
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

  // ---------------------------------------------------------------------------
  // OHLCV freshness — R-03 weekend-aware logic
  //
  // Legacy path:  ohlcv_last_date only  → treat as midnight UTC (old behaviour)
  // New path:     ohlcv_is_fresh + ohlcv_last_session available from backend
  //               → use NYSE close time of last_session as the age reference
  //               → mark source as fresh=true so it contributes 0 to maxAgeH
  //                 (prevents a Friday close showing "3d old" on Monday morning)
  // ---------------------------------------------------------------------------
  if (health?.data_freshness?.ohlcv_last_date) {
    const lastDate    = health.data_freshness.ohlcv_last_date
    const lastSession = health.data_freshness.ohlcv_last_session
    const isFresh     = health.data_freshness.ohlcv_is_fresh

    // Reference timestamp for age display:
    //   fresh  → approximate NYSE close of last_session (20:00 UTC ≈ 16:00 ET)
    //   stale  → midnight of ohlcv_last_date (preserves legacy behaviour)
    const asOf = (isFresh && lastSession)
      ? lastSession + 'T20:00:00Z'
      : lastDate + 'T00:00:00Z'

    // Tooltip label makes the comparison explicit for the user
    const label = (isFresh && lastSession)
      ? `Fresh \u2014 last session ${lastSession} (data ${lastDate})`
      : `Stale \u2014 last data ${lastDate}${lastSession ? `, session ${lastSession}` : ''}`

    sources.push({ name: 'OHLCV', asOf, label, fresh: isFresh === true })
  }

  // Nothing to show until at least one source has data
  if (sources.length === 0) return null

  // For colour-band: fresh OHLCV contributes 0 age (it IS up-to-date with last close)
  const maxAgeH = Math.max(...sources.map((s) => s.fresh ? 0 : ageHours(s.asOf)))
  const oldestSource = sources.reduce((a, b) => {
    const aH = a.fresh ? 0 : ageHours(a.asOf)
    const bH = b.fresh ? 0 : ageHours(b.asOf)
    return aH >= bH ? a : b
  })

  const colorClass =
    maxAgeH < 4
      ? 'bg-green-700/30 text-green-300 border-green-700/40'
      : maxAgeH < 24
      ? 'bg-amber-700/30 text-amber-200 border-amber-700/40'
      : 'bg-red-800/30 text-red-200 border-red-800/40'

  const emoji = maxAgeH < 4 ? '\ud83d\udfe2' : maxAgeH < 24 ? '\ud83d\udfe1' : '\ud83d\udd34'

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-mono border cursor-pointer select-none ${colorClass}`}
        aria-label="Data freshness"
      >
        {emoji} Data&nbsp;&middot;&nbsp;{relativeTime(oldestSource.asOf)} old
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 z-50 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-3 shadow-xl min-w-[200px]">
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-2 font-semibold">
            Data sources
          </div>
          {sources.map((s) => (
            <div key={s.name} className="py-0.5">
              <div className="flex justify-between gap-4 text-[11px] font-mono">
                <span className="text-[var(--color-text-muted)]">{s.name}</span>
                <span className="text-[var(--color-text)] tabular-nums">
                  {s.fresh ? '\u2705 fresh' : relativeTime(s.asOf)}
                </span>
              </div>
              {s.label && (
                <div className="text-[10px] text-[var(--color-text-muted)] opacity-70 mt-0.5 leading-tight">
                  {s.label}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
