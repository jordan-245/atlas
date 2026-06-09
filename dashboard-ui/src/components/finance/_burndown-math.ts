/**
 * Shared math helpers for the burn-down (B4) finance components.
 *
 * Kept dependency-free + pure so each component (BurnDownMountain,
 * CategoryBurnGrid, WhatIfPanel, HistoricalOverspend) can import what it
 * needs without circular references. No JSX, no React.
 */
import type { PacePoint, MonthlyComparison, SpendCategory, CategoryTrend } from '../../api/types'

/** Day-of-month from an ISO date string, or null if unparseable. */
export function dayOfMonth(iso: string | undefined): number | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.getUTCDate()
}

/** Total days in the month of the given ISO date. Default: 31 (defensive). */
export function daysInMonth(iso: string | undefined): number {
  if (!iso) return 31
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 31
  return new Date(d.getUTCFullYear(), d.getUTCMonth() + 1, 0).getUTCDate()
}

export interface BurnDownPoint {
  day: number
  actual: number
  budget: number
}

/**
 * Convert pace_data points into burn-down chart points keyed by day-of-month.
 * `pace_data[].budget` is the API's pre-computed budget pace; we keep it.
 * Returns points sorted by day ascending.
 */
export function paceToBurnDown(paceData: PacePoint[] | undefined): BurnDownPoint[] {
  if (!paceData) return []
  return paceData
    .map((p) => {
      const day = dayOfMonth(p.date)
      if (day == null) return null
      return { day, actual: p.actual ?? 0, budget: p.budget ?? 0 }
    })
    .filter((p): p is BurnDownPoint => p != null)
    .sort((a, b) => a.day - b.day)
}

/**
 * Linear projection from the last actual sample to the end of the month at
 * `dailyAvg` per day. Returns the projected total. If the last day is already
 * the end-of-month, just returns the last actual.
 */
export function projectMonthEnd(
  points: BurnDownPoint[],
  totalBudget: number,
  dailyAvg: number,
  totalDays: number,
): { projected: number; lastDay: number; overBudget: boolean; diff: number } {
  if (points.length === 0) {
    return { projected: 0, lastDay: 0, overBudget: false, diff: -totalBudget }
  }
  const last = points[points.length - 1]
  const remainingDays = Math.max(0, totalDays - last.day)
  const projected = last.actual + remainingDays * Math.max(0, dailyAvg)
  return {
    projected,
    lastDay: last.day,
    overBudget: projected > totalBudget,
    diff: projected - totalBudget, // positive = over budget
  }
}

/** Format AUD currency (whole dollars) — `$5,180` etc. */
export function fmtCcyShort(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  return `${sign}$${abs.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}

/** Format AUD currency with sign + 2dp — `+$4,239.68` / `-$395.00`. */
export function fmtSignedCcyShort(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  const abs = Math.abs(v)
  const sign = v >= 0 ? '+' : '-'
  return `${sign}$${abs.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}

/**
 * Build the diff polygon paths needed to render the green-cushion (actual
 * under budget) and red-overspend (actual over budget) areas between the
 * actual line and the budget line. Uses the same coordinate system the
 * caller draws the lines in.
 *
 * Returns two SVG `points` strings for `<polygon>`. Either can be empty.
 */
export function buildDiffPolygons(
  points: BurnDownPoint[],
  toX: (day: number) => number,
  toY: (dollars: number) => number,
): { greenCushion: string; redOverspend: string } {
  if (points.length === 0) return { greenCushion: '', redOverspend: '' }

  // Sample points + their crossings (interpolate where actual crosses budget)
  type Pt = { day: number; actual: number; budget: number }
  const samples: Pt[] = [...points]
  const crossings: Pt[] = []
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]
    const b = points[i]
    const da = a.actual - a.budget
    const db = b.actual - b.budget
    if (da === 0 || db === 0) continue
    if ((da < 0 && db > 0) || (da > 0 && db < 0)) {
      // Linear interp: find t where actual==budget
      const t = da / (da - db)
      const day = a.day + (b.day - a.day) * t
      const v = a.actual + (b.actual - a.actual) * t
      crossings.push({ day, actual: v, budget: v })
    }
  }
  const all = [...samples, ...crossings].sort((p, q) => p.day - q.day)

  // For each segment, decide if actual > budget (red) or < (green).
  // Build polygon points: actual path forward, then budget path back.
  const greenSegments: Pt[][] = []
  const redSegments: Pt[][] = []
  let cur: Pt[] = []
  let curIsRed: boolean | null = null
  for (const p of all) {
    const isRed = p.actual > p.budget
    if (curIsRed == null) {
      curIsRed = isRed
      cur = [p]
    } else if (isRed === curIsRed) {
      cur.push(p)
    } else {
      if (cur.length >= 2) (curIsRed ? redSegments : greenSegments).push(cur)
      cur = [p]
      curIsRed = isRed
    }
  }
  if (cur.length >= 2 && curIsRed != null) {
    (curIsRed ? redSegments : greenSegments).push(cur)
  }

  const toPolyPoints = (seg: Pt[]): string => {
    if (seg.length < 2) return ''
    const fwd = seg.map((p) => `${toX(p.day)},${toY(p.actual)}`)
    const rev = [...seg].reverse().map((p) => `${toX(p.day)},${toY(p.budget)}`)
    return [...fwd, ...rev].join(' ')
  }

  return {
    greenCushion: greenSegments.map(toPolyPoints).filter(Boolean).join(' '),
    redOverspend: redSegments.map(toPolyPoints).filter(Boolean).join(' '),
  }
}

/**
 * Compose per-parent-category "this-month vs typical" view.
 * "Typical" is sourced from `category_trends[].last_month` when available
 * (matched by `category` key); fall back to `this_month` (which makes the
 * mini-burndown render flat — better than crashing).
 */
export interface CategoryBurnRow {
  category: string
  label: string
  thisMonth: number
  typical: number
  overshootPct: number // (this - typical) / typical * 100; 0 if no typical
  state: 'over' | 'near' | 'under'
}

export function buildCategoryRows(
  parents: SpendCategory[] | undefined,
  trends: CategoryTrend[] | undefined,
): CategoryBurnRow[] {
  if (!parents) return []
  const trendByCat = new Map<string, CategoryTrend>()
  for (const t of trends ?? []) if (t.category) trendByCat.set(t.category, t)

  return parents
    .filter((p): p is SpendCategory & { category: string } => p.category != null)
    .map((p) => {
      const trend = trendByCat.get(p.category)
      const thisMonth = p.amount ?? trend?.this_month ?? 0
      const typical = trend?.last_month ?? thisMonth
      const overshootPct = typical > 0 ? ((thisMonth - typical) / typical) * 100 : 0
      const state: 'over' | 'near' | 'under' =
        overshootPct >= 10 ? 'over' : overshootPct >= -5 ? 'near' : 'under'
      return {
        category: p.category,
        label: p.label ?? p.category,
        thisMonth,
        typical,
        overshootPct,
        state,
      }
    })
    .sort((a, b) => b.thisMonth - a.thisMonth)
}

/**
 * Historical overspend rows from monthly_comparison: each month's spend vs
 * the constant total_monthly_budget. Returned newest-first (matches the API).
 */
export interface HistoricalRow {
  month: string
  spending: number
  budget: number
  over: boolean
  diff: number
}

export function buildHistoricalRows(
  comparison: MonthlyComparison[] | undefined,
  monthlyBudget: number | undefined,
): HistoricalRow[] {
  if (!comparison || monthlyBudget == null) return []
  return comparison
    .filter((r): r is MonthlyComparison & { month: string } => r.month != null)
    .map((r) => {
      const spending = r.spending ?? 0
      const over = spending > monthlyBudget
      return {
        month: r.month,
        spending,
        budget: monthlyBudget,
        over,
        diff: spending - monthlyBudget,
      }
    })
}

/**
 * Average monthly net savings from monthly_comparison (last N months).
 * Returns 0 if no data. Used by SaverPots (for goal ETAs) and WhatIfPanel
 * (as the baseline savings pace in the cut-and-save projection).
 */
export function avgMonthlyNet(comparison: MonthlyComparison[] | undefined, lookback = 6): number {
  if (!comparison || comparison.length === 0) return 0
  const slice = comparison.slice(0, lookback)
  const nets = slice
    .map((r) => r.net)
    .filter((n): n is number => typeof n === 'number' && !Number.isNaN(n))
  if (nets.length === 0) return 0
  return nets.reduce((a, b) => a + b, 0) / nets.length
}
