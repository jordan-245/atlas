/**
 * useResearchMatrix — React Query hook for GET /api/research-matrix/coverage.
 *
 * Returns the strategy × universe coverage matrix enriched with lifecycle
 * states and days_stale from the backend.
 */

import { useQuery } from '@tanstack/react-query'
import { get } from '../api/client'

// ── Types ─────────────────────────────────────────────────────────────────────

export type CellHealth = 'green' | 'yellow' | 'red' | 'grey'
export type LifecycleState = 'RESEARCH' | 'PAPER' | 'LIVE' | 'RETIRED'

export interface MatrixCell {
  sharpe: number | null
  trades: number | null
  max_dd_pct: number | null
  last_updated: string | null
  days_stale: number | null
  lifecycle_state: LifecycleState | null
  entered_state_at: string | null
  in_active_config: boolean
  health: CellHealth
}

/** One row of the matrix (one strategy, N universe cells). */
export interface MatrixRow {
  strategy: string
  cells: (MatrixCell | null)[]
}

export interface ResearchMatrixResponse {
  strategies: string[]
  universes: string[]
  matrix: MatrixRow[]
  generated_at: string
}

// ── Query key ─────────────────────────────────────────────────────────────────

export const RESEARCH_MATRIX_KEY = ['research-matrix', 'coverage'] as const

// ── Hook ──────────────────────────────────────────────────────────────────────

/**
 * Fetch the strategy × universe research coverage matrix.
 * Refreshes every 5 minutes; stale after 2 minutes.
 */
export function useResearchMatrix(enabled = true) {
  return useQuery({
    queryKey: RESEARCH_MATRIX_KEY,
    queryFn: () => get<ResearchMatrixResponse>('/api/research-matrix/coverage'),
    enabled,
    refetchInterval: 5 * 60 * 1000,
    staleTime: 2 * 60 * 1000,
  })
}
