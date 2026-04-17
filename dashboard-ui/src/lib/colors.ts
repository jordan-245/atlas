export const STRATEGY_COLORS: Record<string, string> = {
  MR: '#6366f1',
  TF: '#22c55e',
  OG: '#f59e0b',
  MB: '#ec4899',
  SR: '#14b8a6',
  STMR: '#a855f7',
  ConnorsRSI2: '#ef4444',
  connors_rsi2: '#ef4444',
  mean_reversion: '#6366f1',
  trend_following: '#22c55e',
  opening_gap: '#f59e0b',
  momentum_breakout: '#ec4899',
  support_resistance: '#14b8a6',
  short_term_mr: '#a855f7',
}

export const REGIME_COLORS: Record<string, string> = {
  bull_quiet: '#22c55e',
  bull_volatile: '#84cc16',
  bear_quiet: '#ef4444',
  bear_volatile: '#dc2626',
  neutral: '#a1a1aa',
  transition_uncertain: '#f59e0b',
}

export function getStrategyColor(name: string | null | undefined): string {
  if (!name) return '#a1a1aa'
  return STRATEGY_COLORS[name] ?? STRATEGY_COLORS[String(name).toLowerCase()] ?? '#a1a1aa'
}

export function getRegimeColor(state: string | null | undefined): string {
  if (!state) return '#a1a1aa'
  return REGIME_COLORS[state] ?? REGIME_COLORS[String(state).toLowerCase()] ?? '#a1a1aa'
}
