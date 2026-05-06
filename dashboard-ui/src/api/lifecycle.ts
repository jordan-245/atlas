/**
 * Strategy lifecycle API — typed fetch wrappers and React Query hooks.
 * Endpoints: /api/strategy-lifecycle/*
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { get, post } from './client'
import { qk } from './keys'

// ── Types ──────────────────────────────────────────────────────────

export type LifecycleState = 'RESEARCH' | 'PAPER' | 'LIVE' | 'RETIRED'

export type LifecycleActionType =
  | 'promote_paper'
  | 'promote_live'
  | 'rollback'
  | 'retire'
  | 'revive'
  | 'rollback_paper'

export interface LifecycleRow {
  strategy: string
  universe: string
  state: LifecycleState
  entered_state_at: string | null
  prev_state: LifecycleState | null
  transition_reason: string | null
  paper_start_date: string | null
  paper_end_date: string | null
  research_sharpe: number | null
  paper_sharpe: number | null
  paper_trades_count: number | null
  days_in_paper: number | null
  gap: number | null
  live_sharpe: number | null
  live_trades_count: number | null
}

export interface LifecycleResponse {
  rows: LifecycleRow[]
}

export interface HistoryEntry {
  from_state: LifecycleState | null
  to_state: LifecycleState
  transitioned_at: string
  reason: string | null
  operator: string | null
  auto_promotion_id: number | null
}

export interface HistoryResponse {
  history: HistoryEntry[]
}

export interface TransitionRequest {
  strategy: string
  universe: string
  new_state: LifecycleState
  reason: string
  force?: boolean
}

export interface TransitionResponse {
  transitioned: boolean
}

export interface PromotionResponse {
  promoted: boolean
  reason?: string
  gates?: Record<string, boolean>
  paper_sharpe?: number
  research_sharpe?: number
  gap?: number
}

export interface RecentHistoryEntry extends HistoryEntry {
  strategy: string
  universe: string
}

export interface RecentHistoryResponse {
  history: RecentHistoryEntry[]
}

// ── Query keys (rooted under admin.all for cache coherence) ────────

export const lcqk = {
  all:           () => [...qk.admin.all(), 'lifecycle'] as const,
  rows:          () => [...lcqk.all(), 'rows'] as const,
  history:       (strategy: string, universe: string) =>
    [...lcqk.all(), 'history', strategy, universe] as const,
  recentHistory: () => [...lcqk.all(), 'recent-history'] as const,
}

// ── Fetch wrappers ─────────────────────────────────────────────────

export function fetchLifecycle(): Promise<LifecycleResponse> {
  return get<LifecycleResponse>('/api/strategy-lifecycle')
}

export function fetchHistory(strategy: string, universe: string): Promise<HistoryResponse> {
  return get<HistoryResponse>(
    `/api/strategy-lifecycle/${encodeURIComponent(strategy)}/${encodeURIComponent(universe)}/history`,
  )
}

export function transition(body: TransitionRequest): Promise<TransitionResponse> {
  return post<TransitionResponse>('/api/strategy-lifecycle/transition', body)
}

export function promotePaper(strategy: string, universe: string): Promise<PromotionResponse> {
  return post<PromotionResponse>('/api/strategy-lifecycle/promote-paper', { strategy, universe })
}

/** Gracefully degrades to null if the endpoint does not exist yet. */
export function fetchRecentHistory(limit = 20): Promise<RecentHistoryResponse | null> {
  return get<RecentHistoryResponse>(
    `/api/strategy-lifecycle/recent-history?limit=${limit}`,
  ).catch(() => null)
}

// ── Hooks ──────────────────────────────────────────────────────────

export function useLifecycle(enabled = true) {
  return useQuery({
    queryKey:        lcqk.rows(),
    queryFn:         fetchLifecycle,
    enabled,
    refetchInterval: 30_000,
    staleTime:       15_000,
  })
}

export function useLifecycleHistory(strategy: string, universe: string, enabled = true) {
  return useQuery({
    queryKey: lcqk.history(strategy, universe),
    queryFn:  () => fetchHistory(strategy, universe),
    enabled,
    staleTime: 60_000,
  })
}

export function useRecentLifecycleHistory(enabled = true) {
  return useQuery({
    queryKey:        lcqk.recentHistory(),
    queryFn:         () => fetchRecentHistory(20),
    enabled,
    staleTime:       30_000,
    refetchInterval: 60_000,
  })
}

/** Returns a stable callback that invalidates all lifecycle cache. */
export function useInvalidateLifecycle() {
  const qc = useQueryClient()
  return function invalidateLifecycle() {
    void qc.invalidateQueries({ queryKey: lcqk.all() })
  }
}
