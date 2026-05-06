import { useState } from 'react'
import { useOverrideAudit } from '../../api/admin-queries'
import { useRecentLifecycleHistory } from '../../api/lifecycle'
import { RevertButton } from './RevertButton'
import { fmtRelativeTime } from '../../lib/format'
import type { AuditEntry } from '../../api/admin-types'
import type { RecentHistoryEntry } from '../../api/lifecycle'

// ── Config override audit entries ──────────────────────────────────

const ACTION_BADGE: Record<string, string> = {
  create:    'bg-blue-500/15 text-blue-400 border-blue-500/30',
  revert:    'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
  supersede: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  expire:    'bg-zinc-500/15 text-zinc-500 border-zinc-500/30',
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const [expanded, setExpanded] = useState(false)
  const reason = entry.reason ?? ''
  const truncated = reason.length > 80 ? reason.slice(0, 80) + '…' : reason
  const actor = entry.actor.startsWith('human:') ? entry.actor.slice(6) : entry.actor

  return (
    <div className="border-b border-[var(--color-border)]/40 py-2 text-xs">
      <div className="flex items-center gap-2 flex-wrap">
        <span title={entry.ts} className="text-[var(--color-text-muted)] min-w-[80px]">
          {fmtRelativeTime(entry.ts)}
        </span>
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] font-mono border ${ACTION_BADGE[entry.action] ?? ''}`}
        >
          {entry.action}
        </span>
        <span className="text-[var(--color-text-muted)]">{actor}</span>
        <span className="font-mono">
          {entry.scope} {entry.key}
        </span>
        <span className="text-[var(--color-text-muted)]">
          {entry.from_state ?? '—'} → {entry.to_state ?? '—'}
        </span>
        {entry.action === 'create' && entry.override_id != null && (
          <RevertButton overrideId={entry.override_id} label="Revert" />
        )}
      </div>
      {reason && (
        <div
          className="mt-1 text-[var(--color-text-muted)] cursor-pointer pl-2"
          onClick={() => setExpanded(!expanded)}
          title="Click to expand"
        >
          Reason: {expanded ? reason : truncated}
        </div>
      )}
    </div>
  )
}

// ── Lifecycle change entries ────────────────────────────────────────

const LC_STATE_BADGE: Record<string, string> = {
  RESEARCH: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  PAPER:    'bg-amber-500/15 text-amber-400 border-amber-500/30',
  LIVE:     'bg-green-500/15 text-green-400 border-green-500/30',
  RETIRED:  'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
}

function LifecycleRow({ entry }: { entry: RecentHistoryEntry }) {
  const [expanded, setExpanded] = useState(false)
  const reason = entry.reason ?? ''
  const truncated = reason.length > 80 ? reason.slice(0, 80) + '…' : reason

  return (
    <div className="border-b border-[var(--color-border)]/40 py-2 text-xs">
      <div className="flex items-center gap-2 flex-wrap">
        <span
          title={entry.transitioned_at}
          className="text-[var(--color-text-muted)] min-w-[80px]"
        >
          {fmtRelativeTime(entry.transitioned_at)}
        </span>
        {/* Source pill */}
        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono border bg-purple-500/15 text-purple-400 border-purple-500/30">
          lifecycle
        </span>
        {entry.operator && (
          <span className="text-[var(--color-text-muted)]">{entry.operator}</span>
        )}
        <span className="font-mono">
          {entry.strategy} · {entry.universe}
        </span>
        {/* State transition */}
        <span className="flex items-center gap-1">
          {entry.from_state && (
            <>
              <span className={`px-1.5 py-0.5 rounded font-mono border text-[10px] ${LC_STATE_BADGE[entry.from_state] ?? ''}`}>
                {entry.from_state}
              </span>
              <span className="text-[var(--color-text-muted)]">→</span>
            </>
          )}
          <span className={`px-1.5 py-0.5 rounded font-mono border text-[10px] ${LC_STATE_BADGE[entry.to_state] ?? ''}`}>
            {entry.to_state}
          </span>
        </span>
        {entry.auto_promotion_id != null && (
          <span className="text-[var(--color-text-muted)] text-[10px]">
            auto #{entry.auto_promotion_id}
          </span>
        )}
      </div>
      {reason && (
        <div
          className="mt-1 text-[var(--color-text-muted)] cursor-pointer pl-2"
          onClick={() => setExpanded(!expanded)}
          title="Click to expand"
        >
          Reason: {expanded ? reason : truncated}
        </div>
      )}
    </div>
  )
}

// ── Panel ──────────────────────────────────────────────────────────

export function RecentChangesPanel() {
  const { data, isLoading, error } = useOverrideAudit({ limit: 50 })
  const { data: lcData, isLoading: lcLoading, error: lcError } = useRecentLifecycleHistory(true)

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card space-y-4">

      {/* ── Config override audit ── */}
      <section>
        <h3 className="text-sm font-semibold mb-3">Config override changes (last 50)</h3>
        {isLoading && (
          <div className="text-xs text-[var(--color-text-muted)]">Loading audit log…</div>
        )}
        {error && (
          <div className="text-xs text-red-400">
            Failed to load: {(error as Error).message}
          </div>
        )}
        {data?.audit.length === 0 && (
          <div className="text-xs text-[var(--color-text-muted)]">No override changes yet.</div>
        )}
        {data?.audit.map((entry) => (
          <AuditRow key={entry.id} entry={entry} />
        ))}
      </section>

      {/* ── Lifecycle transitions ── */}
      <section>
        <h3 className="text-sm font-semibold mb-3">Lifecycle changes (last 20)</h3>
        {lcLoading && (
          <div className="text-xs text-[var(--color-text-muted)]">Loading lifecycle history…</div>
        )}
        {lcError && (
          <div className="text-xs text-red-400">
            Failed to load: {(lcError as Error).message}
          </div>
        )}
        {/* null response means endpoint not available yet — degrade gracefully */}
        {lcData === null && !lcLoading && !lcError && (
          <div className="text-xs text-[var(--color-text-muted)]">
            Lifecycle history endpoint not available yet.
          </div>
        )}
        {lcData?.history.length === 0 && (
          <div className="text-xs text-[var(--color-text-muted)]">No lifecycle transitions yet.</div>
        )}
        {lcData?.history.map((entry, idx) => (
          <LifecycleRow key={`${entry.strategy}.${entry.universe}.${idx}`} entry={entry} />
        ))}
      </section>
    </div>
  )
}
