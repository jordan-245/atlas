/**
 * useStrategyLifecycle — React Query hook for the strategy lifecycle API.
 *
 * Wraps GET /api/strategy-lifecycle and related endpoints.
 * Re-exports the core types from api/lifecycle.ts for convenience.
 */

import { useQuery } from '@tanstack/react-query'
import { get } from '../api/client'
import type { LifecycleRow, LifecycleResponse } from '../api/lifecycle'

export type { LifecycleRow, LifecycleResponse }

// ── Query key ─────────────────────────────────────────────────────────────────

const LIFECYCLE_KEY = ['strategy-lifecycle'] as const

// ── Hook ─────────────────────────────────────────────────────────────────────

/**
 * Fetch all strategy lifecycle rows from GET /api/strategy-lifecycle.
 * Refreshes every 30 s; stale after 15 s.
 */
export function useStrategyLifecycle(enabled = true) {
  return useQuery({
    queryKey: LIFECYCLE_KEY,
    queryFn: () => get<LifecycleResponse>('/api/strategy-lifecycle'),
    enabled,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}

// ── History hook ──────────────────────────────────────────────────────────────

const HISTORY_KEY = (strategy: string, universe: string) =>
  ['strategy-lifecycle', 'history', strategy, universe] as const

export interface HistoryRow {
  id?: number
  strategy?: string
  universe?: string
  from_state: string | null
  to_state: string
  transitioned_at: string
  reason: string | null
  operator: string | null
  auto_promotion_id: string | null
}

interface HistoryResponse {
  rows: HistoryRow[]
}

/**
 * Fetch transition history for a single (strategy, universe) pair.
 * Fetched lazily (enabled=false by default) and cached for 60 s.
 */
export function useStrategyLifecycleHistory(
  strategy: string,
  universe: string,
  enabled = false,
) {
  return useQuery({
    queryKey: HISTORY_KEY(strategy, universe),
    queryFn: () =>
      get<HistoryResponse>(
        `/api/strategy-lifecycle/${encodeURIComponent(strategy)}/${encodeURIComponent(universe)}/history`,
      ),
    enabled,
    staleTime: 60_000,
  })
}
