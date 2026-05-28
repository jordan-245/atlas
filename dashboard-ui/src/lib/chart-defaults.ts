/**
 * chart-defaults.ts -- Centralized Chart.js theme + options defaults.
 *
 * Every <Chart> wrapper render merges these defaults into the Chart.js
 * options object before passing to react-chartjs-2.  Caller overrides
 * win via deep-merge.
 *
 * Semantic colour tokens (categorical / portfolio / benchmark / grid)
 * still mirror the values in chart-palette.ts so a stepwise migration
 * doesn't break visual identity.
 */

import type { ChartOptions } from 'chart.js'

// ── Categorical palette (5 steps, dark-mode friendly, no sign meaning) ────
export const CATEGORICAL_5 = [
  '#6366f1', // indigo (primary)
  '#14b8a6', // teal
  '#f59e0b', // amber
  '#ec4899', // pink
  '#a855f7', // purple
] as const

export function paletteFor(index: number): string {
  return CATEGORICAL_5[index % CATEGORICAL_5.length]
}

// ── Semantic series tokens ────────────────────────────────────────────────
// Resolved from CSS vars at runtime by reading computed style on document.body.
function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined' || typeof document === 'undefined') return fallback
  const v = getComputedStyle(document.body).getPropertyValue(name).trim()
  return v || fallback
}

export function seriesPortfolio(): string {
  return cssVar('--color-series-portfolio', '#22c55e')
}
export function seriesBenchmark(): string {
  return cssVar('--color-series-benchmark', '#a1a1aa')
}
export function seriesGrid(): string {
  return cssVar('--color-series-grid', 'rgba(255,255,255,0.06)')
}
export function textMuted(): string {
  return cssVar('--color-text-muted', '#8b929d')
}
export function textBody(): string {
  return cssVar('--color-text', '#e6e8eb')
}
export function surface(): string {
  return cssVar('--color-surface', '#14171c')
}
export function border(): string {
  return cssVar('--color-border', '#2a2f37')
}

// ── Severity tokens (knowledge layer) ─────────────────────────────────────
export const SEVERITY_CRITICAL = '#ef4444'
export const SEVERITY_MAJOR    = '#f59e0b'
export const SEVERITY_MINOR    = '#8b929d'

// ── Default Chart.js options ──────────────────────────────────────────────
// Spread into every Chart wrapper render.  Animations OFF by default
// (ops dashboards prefer "instant" responsiveness; opt back in per-chart
// with `options.animation = { duration: 300 }`).
export function defaultChartOptions(): ChartOptions<'line' | 'bar' | 'doughnut'> {
  const tickColor = textMuted()
  const gridColor = seriesGrid()
  const tooltipBg = surface()
  const tooltipBorder = border()
  const bodyColor = textBody()

  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,                   // off by default; opt-in per chart
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        display: false,                  // most callers don't want a legend
        position: 'top',
        labels: {
          color: bodyColor,
          font: { size: 11, family: 'inherit' },
          boxWidth: 10,
          boxHeight: 10,
          padding: 12,
          usePointStyle: true,
        },
      },
      tooltip: {
        enabled: true,
        backgroundColor: tooltipBg,
        borderColor: tooltipBorder,
        borderWidth: 1,
        titleColor: bodyColor,
        bodyColor,
        titleFont: { size: 11, weight: 600, family: 'inherit' },
        bodyFont: { size: 11, family: 'JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace' },
        padding: 8,
        cornerRadius: 6,
        boxPadding: 4,
        displayColors: true,
      },
    },
    scales: {
      x: {
        ticks: {
          color: tickColor,
          font: { size: 10, family: 'inherit' },
          autoSkipPadding: 16,
          maxRotation: 0,
        },
        grid: { display: false },
        border: { color: gridColor },
      },
      y: {
        ticks: {
          color: tickColor,
          font: { size: 10, family: 'inherit' },
          padding: 6,
        },
        grid: {
          color: gridColor,
          drawTicks: false,
        },
        border: { display: false },
      },
    },
  } as ChartOptions<'line' | 'bar' | 'doughnut'>
}

// ── Deep-merge helper ─────────────────────────────────────────────────────
// Chart.js options nest 2-3 levels deep; spread-merge would clobber.
// Small, focused merge for ChartOptions only.
type DeepRecord = Record<string, unknown>

export function mergeOptions<T extends object>(base: T, override?: Partial<T>): T {
  if (!override) return base
  const out: DeepRecord = {}
  const baseRec = base as unknown as DeepRecord
  const overRec = override as unknown as DeepRecord

  for (const key of Object.keys(baseRec)) {
    const a = baseRec[key]
    const b = overRec[key]
    if (b === undefined) {
      out[key] = a
    } else if (
      a && typeof a === 'object' && !Array.isArray(a) &&
      b && typeof b === 'object' && !Array.isArray(b)
    ) {
      out[key] = mergeOptions(a as object, b as object)
    } else {
      out[key] = b
    }
  }
  for (const key of Object.keys(overRec)) {
    if (!(key in out)) out[key] = overRec[key]
  }
  return out as T
}
