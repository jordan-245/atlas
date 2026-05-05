// Admin API types — match shapes returned by services/api/admin.py
// All fields conservative: server returns null for missing; UI must handle.

export type UniverseState = 'live' | 'passive' | 'disabled'
export type StrategyState = 'enabled' | 'disabled'

export interface ActiveOverride {
  id: number
  scope: 'universe' | 'strategy'
  key: string
  state: string  // UniverseState | StrategyState
  reason: string | null
  created_by: string
  created_at: string  // ISO 8601 (sqlite datetime format: "YYYY-MM-DD HH:MM:SS")
  expires_at: string | null
  prev_state: string | null
  active: number  // 1
}

export interface UniverseAdminRow {
  market_id: string
  effective_state: UniverseState
  config_state: UniverseState
  override: ActiveOverride | null
  open_positions: number
  last_trade_at: string | null
  starting_equity: number | null
  current_equity: number | null
  version: string | null
}

export interface StrategyAdminRow {
  market_id: string
  strategy: string
  effective_enabled: boolean
  config_enabled: boolean
  weight: number
  override: ActiveOverride | null
  open_positions: number
  trades_30d: number
  pnl_30d: number
  lifecycle: 'ACTIVE' | 'WATCH' | 'RETIRED' | 'UNKNOWN'
}

export interface AuditEntry {
  id: number
  ts: string
  override_id: number | null
  scope: 'universe' | 'strategy'
  key: string
  action: 'create' | 'revert' | 'expire' | 'supersede'
  from_state: string | null
  to_state: string | null
  reason: string | null
  actor: string
  source: 'dashboard' | 'cli' | 'telegram' | 'sweep'
  remote_ip: string | null
}

export interface UniversesResponse { universes: UniverseAdminRow[] }
export interface StrategiesResponse { strategies: StrategyAdminRow[] }
export interface AuditResponse { audit: AuditEntry[]; next_cursor: string | null }

// ── Mutation request bodies ────────────────────────────────────────────
export interface UniverseStateChangeRequest {
  state: UniverseState
  reason: string  // ≥10 chars
  expires_at?: string | null  // omitted = backend default 30d; explicit null = permanent
  confirm_token?: string  // required when target universe is currently 'live' (production)
  i_understand: boolean  // must be true
}

export interface StrategyStateChangeRequest {
  state: StrategyState
  reason: string
  expires_at?: string | null
  i_understand: boolean
}

export interface RevertOverrideRequest {
  reason: string  // ≥10 chars
}

export interface MutationOk {
  ok: true
  override_id?: number
  reverted_override_id?: number
  market_id?: string
  strategy?: string
  from_state: string
  to_state: string
  expires_at?: string | null
  source?: string
  scope?: string
  key?: string
}
