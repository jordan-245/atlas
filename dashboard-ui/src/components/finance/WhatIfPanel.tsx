/**
 * WhatIfPanel.tsx -- The "what-if" carrot panel from Variant B4.
 *
 * Premise: the user tends to overspend. Show them, with a slider, how much
 * sooner their largest goal saver fills if they cut $X/month from spending.
 * The narrative is the headline; a small comparison bar visualises it.
 *
 * "Goal saver" here means a long-term goal account (Travel, Emergency,
 * Savings, Invest, House deposit, ...). Fortnight budget buckets (Rent,
 * Food, Phone, Fuel, Gym, AI, ...) live under the same Up Bank `saver`
 * type but are filtered out via `isGoalAccount` from `_goal-classifier.ts`
 * so they never appear in this panel.
 *
 * Reads:
 *   - accounts[]: to find the largest goal saver
 *   - avgMonthlyNet:  baseline savings pace
 *   - monthlyComparison: passed through but currently only used for the
 *     "based on last 6 months" caption. (No comparison sparkline yet --
 *     keeping the panel focused on the slider + narrative.)
 *
 * Saver targets are stored client-side (Up Bank doesn't return goals), so
 * we read them via useSaverTargets().
 */
import { useMemo, useState } from 'react'
import type { FinanceAccount, MonthlyComparison } from '../../api/types'
import { useSaverTargets } from '../../hooks/useSaverTargets'
import { fmtCcyShort } from './_burndown-math'
import { isGoalAccount } from './_goal-classifier'

interface WhatIfPanelProps {
  accounts: FinanceAccount[]
  avgMonthlyNet: number
  monthlyComparison: MonthlyComparison[]
}

// Slider config -- evenly stepped, unevenly labelled.
const SLIDER_MIN = 0
const SLIDER_MAX = 500
const SLIDER_STEP = 10
const SLIDER_DEFAULT = 100
const TICK_VALUES = [0, 100, 200, 300, 500] as const

// Comparison-bar config.
const MAX_MONTHS_DISPLAYED = 24

interface LargestSaver {
  name: string
  balance: number
  target: number | undefined
}

/**
 * Pick the goal-type saver account with the largest balance. Returns the
 * looked-up client-side target alongside. Budget buckets are filtered out
 * via `isGoalAccount`; returns null if no goal saver exists.
 */
function pickLargestSaver(
  accounts: FinanceAccount[],
  targetsMap: Record<string, { target?: number }>,
): LargestSaver | null {
  const savers = accounts.filter(
    (a): a is FinanceAccount & { name: string; balance: number } => {
      if (a.type !== 'saver') return false
      if (typeof a.balance !== 'number' || typeof a.name !== 'string') return false
      const stored = targetsMap[a.name]?.target
      const hasStoredTarget =
        typeof stored === 'number' && Number.isFinite(stored) && stored > 0
      return isGoalAccount(a.name, hasStoredTarget)
    },
  )
  if (savers.length === 0) return null
  const largest = savers.reduce((a, b) => (b.balance > a.balance ? b : a))
  const entry = targetsMap[largest.name]
  return { name: largest.name, balance: largest.balance, target: entry?.target }
}

/**
 * Months to fill from `balance` to `target` at the given monthly net.
 * Returns null if any input is non-positive/missing or remaining <= 0.
 */
function monthsToFill(
  balance: number,
  target: number | undefined,
  monthlyNet: number,
): number | null {
  if (target == null || !Number.isFinite(target)) return null
  const remaining = target - balance
  if (remaining <= 0) return 0
  if (monthlyNet <= 0 || !Number.isFinite(monthlyNet)) return null
  return remaining / monthlyNet
}

export function WhatIfPanel({
  accounts,
  avgMonthlyNet,
  monthlyComparison,
}: WhatIfPanelProps) {
  // We don't graph monthlyComparison; it's surfaced via the muted caption.
  void monthlyComparison

  const targetsMap = useSaverTargets()
  const [cut, setCut] = useState<number>(SLIDER_DEFAULT)

  const largestSaver = useMemo(
    () => pickLargestSaver(accounts, targetsMap),
    [accounts, targetsMap],
  )

  // ---------- empty state: negative or zero average savings ----------
  if (avgMonthlyNet <= 0 || !Number.isFinite(avgMonthlyNet)) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
        <div className="flex items-center justify-between mb-3">
          <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold">
            WHAT IF YOU SAVED MORE A MONTH?
          </div>
        </div>
        <div className="text-[13px] text-[var(--color-text-muted)] leading-relaxed">
          Your average monthly savings is currently zero or negative -- fix that first.
        </div>
      </div>
    )
  }

  const baseline = avgMonthlyNet
  const withCut = avgMonthlyNet + cut

  // ---------- months comparison ----------
  const baseMonths = largestSaver
    ? monthsToFill(largestSaver.balance, largestSaver.target, baseline)
    : null
  const cutMonths = largestSaver
    ? monthsToFill(largestSaver.balance, largestSaver.target, withCut)
    : null

  // Sentence math: floor at 0; not-a-number -> 0.
  let monthsSooner = 0
  if (baseMonths != null && cutMonths != null) {
    const raw = baseMonths - cutMonths
    monthsSooner = Number.isFinite(raw) && raw > 0 ? Math.round(raw) : 0
  }

  const hasGoal =
    largestSaver != null &&
    largestSaver.target != null &&
    Number.isFinite(largestSaver.target) &&
    largestSaver.target > 0

  // Bar widths as fraction of MAX_MONTHS_DISPLAYED, clamped 0..1.
  const baseFrac =
    baseMonths != null ? Math.min(1, Math.max(0, baseMonths / MAX_MONTHS_DISPLAYED)) : 0
  const cutFrac =
    cutMonths != null ? Math.min(1, Math.max(0, cutMonths / MAX_MONTHS_DISPLAYED)) : 0
  const baseOverflow = baseMonths != null && baseMonths > MAX_MONTHS_DISPLAYED
  const cutOverflow = cutMonths != null && cutMonths > MAX_MONTHS_DISPLAYED

  const showComparison =
    hasGoal &&
    baseMonths != null &&
    cutMonths != null &&
    baseMonths > 0 &&
    Number.isFinite(baseMonths) &&
    Number.isFinite(cutMonths)

  // ---------- styled slider class ----------
  // Tailwind arbitrary-variant selectors target the WebKit + Mozilla pseudo
  // elements directly. The track + thumb each get their own dimensions/colours.
  // Track height 6px, thumb 16px circle. Filled portion: --color-green.
  const sliderClasses = [
    'w-full appearance-none bg-transparent cursor-pointer',
    'focus:outline-none',
    // WebKit track
    '[&::-webkit-slider-runnable-track]:h-[6px]',
    '[&::-webkit-slider-runnable-track]:rounded-full',
    '[&::-webkit-slider-runnable-track]:bg-[var(--color-surface-alt)]',
    // WebKit thumb
    '[&::-webkit-slider-thumb]:appearance-none',
    '[&::-webkit-slider-thumb]:h-[16px]',
    '[&::-webkit-slider-thumb]:w-[16px]',
    '[&::-webkit-slider-thumb]:rounded-full',
    '[&::-webkit-slider-thumb]:bg-[var(--color-green)]',
    '[&::-webkit-slider-thumb]:border-2',
    '[&::-webkit-slider-thumb]:border-[var(--color-surface)]',
    '[&::-webkit-slider-thumb]:shadow',
    '[&::-webkit-slider-thumb]:mt-[-5px]', // centre 16px thumb on 6px track
    // Firefox track
    '[&::-moz-range-track]:h-[6px]',
    '[&::-moz-range-track]:rounded-full',
    '[&::-moz-range-track]:bg-[var(--color-surface-alt)]',
    // Firefox progress (filled portion)
    '[&::-moz-range-progress]:h-[6px]',
    '[&::-moz-range-progress]:rounded-full',
    '[&::-moz-range-progress]:bg-[var(--color-green)]',
    // Firefox thumb
    '[&::-moz-range-thumb]:h-[16px]',
    '[&::-moz-range-thumb]:w-[16px]',
    '[&::-moz-range-thumb]:rounded-full',
    '[&::-moz-range-thumb]:bg-[var(--color-green)]',
    '[&::-moz-range-thumb]:border-2',
    '[&::-moz-range-thumb]:border-[var(--color-surface)]',
  ].join(' ')

  // WebKit doesn't expose a ::-webkit-slider-runnable-track progress pseudo,
  // so we paint the filled portion via a backgroundImage gradient on the input.
  // The gradient is set inline because the percentage is dynamic.
  const fillPct = ((cut - SLIDER_MIN) / (SLIDER_MAX - SLIDER_MIN)) * 100
  const sliderStyle = {
    backgroundImage: `linear-gradient(to right, var(--color-green) 0%, var(--color-green) ${fillPct}%, var(--color-surface-alt) ${fillPct}%, var(--color-surface-alt) 100%)`,
    backgroundRepeat: 'no-repeat',
    backgroundSize: '100% 6px',
    backgroundPosition: 'center',
    height: '16px',
  } as const

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-muted)] font-semibold">
          WHAT IF YOU SAVED {fmtCcyShort(cut)} MORE A MONTH?
        </div>
        <div className="text-[11px] text-[var(--color-text-muted)]">based on last 6 months</div>
      </div>

      {/* Slider + tick row */}
      <div className="mb-4">
        <input
          type="range"
          min={SLIDER_MIN}
          max={SLIDER_MAX}
          step={SLIDER_STEP}
          value={cut}
          onChange={(e) => {
            const n = Number(e.target.value)
            if (Number.isFinite(n)) setCut(n)
          }}
          className={sliderClasses}
          style={sliderStyle}
          aria-label="Monthly spending cut"
        />
        <div className="relative mt-2 h-4">
          {TICK_VALUES.map((v) => {
            const pct = ((v - SLIDER_MIN) / (SLIDER_MAX - SLIDER_MIN)) * 100
            return (
              <span
                key={v}
                className="absolute text-[10px] text-[var(--color-text-muted)] font-mono tabular-nums -translate-x-1/2"
                style={{ left: `${pct}%` }}
              >
                ${v}
              </span>
            )
          })}
        </div>
      </div>

      {/* Narrative readout */}
      <div className="text-[14px] leading-relaxed text-[var(--color-text)] mb-4">
        Cutting{' '}
        <span className="font-mono tabular-nums text-[var(--color-green)] font-semibold">
          {fmtCcyShort(cut)}
        </span>
        /mo from spending grows your monthly savings from{' '}
        <span className="font-mono tabular-nums">{fmtCcyShort(baseline)}</span> to{' '}
        <span className="font-mono tabular-nums text-[var(--color-green)] font-semibold">
          {fmtCcyShort(withCut)}
        </span>
        .
        <br />
        {hasGoal && largestSaver != null && largestSaver.target != null ? (
          <>
            At that pace,{' '}
            <span className="font-semibold">{largestSaver.name}</span> reaches its{' '}
            <span className="font-mono tabular-nums">{fmtCcyShort(largestSaver.target)}</span> goal{' '}
            <span className="font-mono tabular-nums text-[var(--color-green)] font-semibold">
              {monthsSooner}
            </span>{' '}
            month{monthsSooner === 1 ? '' : 's'} sooner.
          </>
        ) : (
          <span className="text-[var(--color-text-muted)]">
            Set a goal on one of your goal accounts to see how much sooner you&rsquo;d hit it.
          </span>
        )}
      </div>

      {/* Visual comparison bar */}
      {showComparison && baseMonths != null && cutMonths != null ? (
        <div className="mt-4 pt-4 border-t border-[var(--color-border)]/60">
          <ComparisonRow
            label="Current pace"
            months={baseMonths}
            frac={baseFrac}
            overflow={baseOverflow}
            color="var(--color-accent)"
          />
          <div className="h-2" />
          <ComparisonRow
            label="With cut"
            months={cutMonths}
            frac={cutFrac}
            overflow={cutOverflow}
            color="var(--color-green)"
          />
          {/* Axis ticks: 0, 6, 12, 18, 24 months */}
          <div className="relative mt-2 h-4 ml-[112px]">
            {[0, 6, 12, 18, 24].map((m) => {
              const pct = (m / MAX_MONTHS_DISPLAYED) * 100
              return (
                <span
                  key={m}
                  className="absolute text-[10px] text-[var(--color-text-muted)] font-mono tabular-nums -translate-x-1/2"
                  style={{ left: `${pct}%` }}
                >
                  {m}mo
                </span>
              )
            })}
          </div>
        </div>
      ) : null}
    </div>
  )
}

// ============================================================================
// internal: one row of the comparison bar
// ============================================================================
interface ComparisonRowProps {
  label: string
  months: number
  frac: number
  overflow: boolean
  color: string
}

function ComparisonRow({ label, months, frac, overflow, color }: ComparisonRowProps) {
  const monthsRounded = Math.round(months)
  const widthPct = Math.max(2, frac * 100) // floor at 2% so a sliver always shows

  return (
    <div className="flex items-center gap-3">
      <div className="w-[100px] shrink-0 text-[11px] text-[var(--color-text-muted)] uppercase tracking-wider">
        {label}
      </div>
      <div className="flex-1 relative h-5 bg-[var(--color-surface-alt)] rounded">
        <div
          className="absolute left-0 top-0 h-full rounded"
          style={{ width: `${widthPct}%`, background: color, opacity: 0.85 }}
        />
        {overflow ? (
          <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-[var(--color-text-muted)] font-mono tabular-nums">
            &hellip;
          </span>
        ) : null}
        <span
          className="absolute right-[-44px] top-1/2 -translate-y-1/2 text-[11px] font-mono tabular-nums text-[var(--color-text)]"
          style={{ minWidth: 40 }}
        >
          {monthsRounded}mo
        </span>
      </div>
    </div>
  )
}
