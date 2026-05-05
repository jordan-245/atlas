import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { get, post } from './client'
import { qk } from './keys'
import type {
  UniversesResponse,
  StrategiesResponse,
  AuditResponse,
  UniverseStateChangeRequest,
  StrategyStateChangeRequest,
  RevertOverrideRequest,
  MutationOk,
} from './admin-types'

const REFETCH_30S = 30_000
const STALE_15S = 15_000

// ── Reads ────────────────────────────────────────────────────────────

export function useAdminUniverses(enabled: boolean = true) {
  return useQuery({
    queryKey: qk.admin.universes(),
    queryFn: () => get<UniversesResponse>('/api/admin/universes'),
    enabled,
    refetchInterval: REFETCH_30S,
    placeholderData: keepPreviousData,
    staleTime: STALE_15S,
  })
}

export function useAdminStrategies(enabled: boolean = true) {
  return useQuery({
    queryKey: qk.admin.strategies(),
    queryFn: () => get<StrategiesResponse>('/api/admin/strategies'),
    enabled,
    refetchInterval: REFETCH_30S,
    placeholderData: keepPreviousData,
    staleTime: STALE_15S,
  })
}

export interface AuditQueryParams {
  scope?: 'universe' | 'strategy'
  key?: string
  limit?: number
  since?: string
}

export function useOverrideAudit(params: AuditQueryParams = {}, enabled: boolean = true) {
  const qs = new URLSearchParams()
  if (params.scope) qs.set('scope', params.scope)
  if (params.key) qs.set('key', params.key)
  if (params.limit) qs.set('limit', String(params.limit))
  if (params.since) qs.set('since', params.since)
  // CRITICAL: backend route is /api/admin/override-audit (NOT /api/admin/audit).
  const url = '/api/admin/override-audit' + (qs.toString() ? '?' + qs.toString() : '')
  return useQuery({
    queryKey: qk.admin.audit(params as Record<string, unknown>),
    queryFn: () => get<AuditResponse>(url),
    enabled,
    refetchInterval: REFETCH_30S,
    placeholderData: keepPreviousData,
    staleTime: STALE_15S,
  })
}

// ── Mutations ────────────────────────────────────────────────────────────

function invalidateAdmin(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: qk.admin.all() })
}

export function useChangeUniverseState() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (vars: { market_id: string; body: UniverseStateChangeRequest }) =>
      post<MutationOk>(`/api/admin/universe/${encodeURIComponent(vars.market_id)}/state`, vars.body),
    onSuccess: () => invalidateAdmin(qc),
  })
}

export function useChangeStrategyState() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (vars: { market_id: string; strategy: string; body: StrategyStateChangeRequest }) =>
      post<MutationOk>(
        `/api/admin/strategy/${encodeURIComponent(vars.market_id)}/${encodeURIComponent(vars.strategy)}/state`,
        vars.body,
      ),
    onSuccess: () => invalidateAdmin(qc),
  })
}

export function useRevertOverride() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (vars: { override_id: number; body: RevertOverrideRequest }) =>
      post<MutationOk>(`/api/admin/override/${vars.override_id}/revert`, vars.body),
    onSuccess: () => invalidateAdmin(qc),
  })
}
