/**
 * chart-palette.ts — Centralized Recharts styling constants.
 *
 * Import these in every chart component instead of repeating inline hex /
 * strokeDasharray strings. Light-mode–aware values use CSS custom properties
 * so they resolve correctly in both themes.
 */

// ---------------------------------------------------------------------------
// Categorical palette — 5 steps, dark-mode friendly, no sign-implication.
// Use index % 5 to wrap. Avoid using #ef4444/#22c55e for non-P&L data —
// those colours already carry "negative/positive" semantics elsewhere.
// ---------------------------------------------------------------------------
export const CATEGORICAL_5 = [
  '#6366f1', // indigo   (primary accent)
  '#14b8a6', // teal
  '#f59e0b', // amber
  '#ec4899', // pink
  '#a855f7', // purple
] as const

export type CategoricalColor = (typeof CATEGORICAL_5)[number]

// ---------------------------------------------------------------------------
// 2-colour series tokens — for portfolio vs benchmark charts
// ---------------------------------------------------------------------------
export const SERIES_PORTFOLIO = 'var(--color-series-portfolio)' // green (dark) / darker green (light)
export const SERIES_BENCHMARK = 'var(--color-series-benchmark)' // zinc-400 (dark) / zinc-500 (light)
export const SERIES_GRID      = 'var(--color-series-grid)'      // very faint — matches --color-border

// ---------------------------------------------------------------------------
// Grid / axis shared props — spread directly onto <CartesianGrid>
// ---------------------------------------------------------------------------
export const CHART_GRID = {
  stroke: 'var(--color-series-grid)',
  strokeDasharray: '3 3',
  vertical: false,
} as const

// ---------------------------------------------------------------------------
// Axis tick shared style — spread as `tick` prop on <XAxis> / <YAxis>
// ---------------------------------------------------------------------------
export const CHART_TICK = {
  fontSize: 10,
  fill: 'var(--color-text-muted)',
} as const

// ---------------------------------------------------------------------------
// Animation defaults — spread onto <Area>, <Line>, <Bar>
// ---------------------------------------------------------------------------
export const CHART_ANIM = {
  isAnimationActive: true,
  animationDuration: 800,
  animationEasing: 'ease-out' as const,
} as const

// ---------------------------------------------------------------------------
// Tooltip cursor style — softened dashes
// ---------------------------------------------------------------------------
export const CHART_CURSOR = {
  stroke: 'var(--color-border)',
  strokeDasharray: '2 4',
} as const

// ---------------------------------------------------------------------------
// Helper — wrap palette for index-based access
// ---------------------------------------------------------------------------
export function paletteFor(index: number): string {
  return CATEGORICAL_5[index % CATEGORICAL_5.length]
}
