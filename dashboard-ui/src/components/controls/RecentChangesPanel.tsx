import { useState } from 'react'
import { useOverrideAudit } from '../../api/admin-queries'
import { RevertButton } from './RevertButton'
import { fmtRelativeTime } from '../../lib/format'
import type { AuditEntry } from '../../api/admin-types'

const ACTION_BADGE: Record<string, string> = {
  create: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  revert: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
  supersede: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  expire: 'bg-zinc-500/15 text-zinc-500 border-zinc-500/30',
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

export function RecentChangesPanel() {
  const { data, isLoading, error } = useOverrideAudit({ limit: 50 })

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 dash-card">
      <h3 className="text-sm font-semibold mb-3">Recent changes (last 50)</h3>
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
    </div>
  )
}
