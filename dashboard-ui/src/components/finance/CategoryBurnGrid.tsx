import type { SpendCategory, CategoryTrend } from '../../api/types'
import { buildCategoryRows, fmtCcyShort } from './_burndown-math'
import type { CategoryBurnRow } from './_burndown-math'

interface CategoryBurnGridProps {
  categories: SpendCategory[]
  trends?: CategoryTrend[]
}

type State = CategoryBurnRow['state']

/** Cap used for the bar so a 250%-overshoot doesn't blow out the layout. */
const BAR_CAP_PCT = 150

const STATE_VAR: Record<State, string> = {
  over:  'var(--color-red)',
  near:  'var(--color-amber)',
  under: 'var(--color-green)',
}

const STATE_LABEL: Record<State, string> = {
  over:  'OVER',
  near:  'NEAR',
  under: 'UNDER',
}

/**
 * CategoryBurnGrid — per-parent-category "this month vs last month" mini cards.
 *
 * Sits beneath the BurnDownMountain hero chart on the Finance tab.
 * Each card screams (or whispers): did this category overspend its typical pace?
 *  - state 'over'  → red stripe, red number, red overshoot segment, OVER chip.
 *  - state 'near'  → amber stripe, default number, amber bar, NEAR chip.
 *  - state 'under' → green stripe, default number, green bar, UNDER chip.
 *
 * The single biggest overspender (max overshootPct + state==='over') also gets a
 * subtle red glow so the eye lands on it first.
 */
export function CategoryBurnGrid({ categories, trends }: CategoryBurnGridProps) {
  const allRows = buildCategoryRows(categories, trends)

  // Filter noise (< $5) but never drop below 4 cards — an empty-feeling grid
  // is worse than a couple of trivial categories.
  const meaningful = allRows.filter((r) => r.thisMonth >= 5)
  const rows: CategoryBurnRow[] = meaningful.length >= 4 ? meaningful : allRows

  // Empty-state: nothing real to show.
  const allZero = rows.every((r) => r.thisMonth === 0 && r.typical === 0)
  if (rows.length === 0 || allZero) {
    return (
      <div className="text-sm text-[var(--color-text-muted)]">No category data yet.</div>
    )
  }

  // Find the single biggest overspender for the glow treatment.
  let topOverIdx = -1
  let topOverPct = -Infinity
  rows.forEach((r, i) => {
    if (r.state === 'over' && r.overshootPct > topOverPct) {
      topOverPct = r.overshootPct
      topOverIdx = i
    }
  })

  // Position (as % of bar width) of the "100% of typical" tick mark.
  // Bar width represents 0..BAR_CAP_PCT of typical, so 100% lands at 100/CAP * 100%.
  const tickLeftPct = (100 / BAR_CAP_PCT) * 100

  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold mb-3">
        CATEGORIES VS LAST MONTH ({rows.length})
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {rows.map((row, i) => (
          <CategoryCard
            key={row.category}
            row={row}
            tickLeftPct={tickLeftPct}
            isTopOver={i === topOverIdx}
          />
        ))}
      </div>
    </div>
  )
}

interface CardProps {
  row: CategoryBurnRow
  tickLeftPct: number
  isTopOver: boolean
}

function CategoryCard({ row, tickLeftPct, isTopOver }: CardProps) {
  const { label, thisMonth, typical, overshootPct, state } = row
  const stateColor = STATE_VAR[state]

  // Bar geometry: how much of the typical has been used, capped at BAR_CAP_PCT.
  const usedPctRaw = typical > 0 ? (thisMonth / typical) * 100 : thisMonth > 0 ? BAR_CAP_PCT : 0
  const usedPct = Math.min(BAR_CAP_PCT, Math.max(0, usedPctRaw))

  // For 'over', we split the fill into 0..100% (muted) + 100..usedPct (red).
  // For 'near' / 'under', it's a single coloured segment.
  // Both segments are positioned as a fraction of the FULL bar (whose width
  // represents BAR_CAP_PCT), so multiply by 100/BAR_CAP_PCT.
  const scale = 100 / BAR_CAP_PCT
  const baseWidthPct = Math.min(100, usedPct) * scale
  const overflowWidthPct = state === 'over' ? Math.max(0, usedPct - 100) * scale : 0

  // Signed delta for the sub-line.
  const sign = overshootPct >= 0 ? '+' : '-'
  const deltaText = `${sign}${Math.abs(overshootPct).toFixed(0)}%`

  const glowShadow = isTopOver
    ? '0 0 0 1px rgba(239,68,68,0.25), 0 0 14px -2px rgba(239,68,68,0.18)'
    : undefined

  const chipBg =
    state === 'over'  ? 'bg-[var(--color-red)]/15'
    : state === 'near' ? 'bg-[var(--color-amber)]/15'
    : 'bg-[var(--color-green)]/15'

  return (
    <div
      data-testid="category-burn-card"
      data-state={state}
      className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card"
      style={{
        borderLeftColor: stateColor,
        borderLeftWidth: 3,
        boxShadow: glowShadow,
      }}
    >
      {/* Top row: label + state chip */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="text-sm font-semibold truncate min-w-0" title={label}>
          {label}
        </div>
        <span
          className={`${chipBg} text-[10px] uppercase tracking-wider font-mono font-semibold rounded px-1.5 py-0.5 flex-shrink-0 leading-none`}
          style={{ color: stateColor }}
        >
          {STATE_LABEL[state]}
        </span>
      </div>

      {/* Big this-month figure */}
      <div
        className="font-mono tabular-nums text-2xl font-semibold"
        style={{ color: state === 'over' ? 'var(--color-red)' : undefined }}
      >
        {fmtCcyShort(thisMonth)}
      </div>

      {/* Sub line: vs typical · ±delta% */}
      <div className="text-xs text-[var(--color-text-muted)] font-mono tabular-nums mt-1">
        vs {fmtCcyShort(typical)} typical · <span style={{ color: stateColor }}>{deltaText}</span>
      </div>

      {/* Visceral horizontal bar */}
      <div
        className="relative w-full mt-3 rounded-full overflow-hidden flex"
        style={{ height: 8, background: 'var(--color-surface-alt)' }}
        aria-hidden="true"
      >
        {/* 0..100% segment — muted for 'over', actual colour otherwise */}
        {baseWidthPct > 0 && (
          <div
            className="h-full"
            style={{
              width: `${baseWidthPct}%`,
              background:
                state === 'over'
                  ? `color-mix(in srgb, var(--color-amber) 55%, transparent)`
                  : stateColor,
              borderRadius: '999px 0 0 999px',
            }}
          />
        )}
        {/* 100..usedPct overflow segment (only for 'over') */}
        {overflowWidthPct > 0 && (
          <div
            className="h-full"
            style={{
              width: `${overflowWidthPct}%`,
              background: 'var(--color-red)',
            }}
          />
        )}
        {/* Tick mark at the 100%-of-typical line */}
        <div
          className="absolute top-0 h-full w-px bg-[var(--color-text-muted)]/40"
          style={{ left: `${tickLeftPct}%` }}
        />
      </div>
    </div>
  )
}
