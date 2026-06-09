import { useLiveState } from '../../api/queries'
import type { LiveDeployed, LiveDailyResult } from '../../api/queries'
import { Skeleton } from '../layout/Skeleton'
import { SectionBoundary } from '../layout/SectionBoundary'

const STATE_COLOR: Record<string, string> = {
  shadow: 'var(--color-text-muted)',
  canary: 'var(--color-amber)',
  live: 'var(--color-green)',
}

function KillSwitchBanner({ blocked, reason, layer }: { blocked: boolean; reason?: string | null; layer?: string | null }) {
  const color = blocked ? 'var(--color-red)' : 'var(--color-green)'
  return (
    <div className="rounded-lg border px-4 py-3" style={{ borderColor: `${color}33`, background: `${color}0d` }}>
      <div className="flex items-center gap-3">
        <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />
        <span className="text-sm font-semibold" style={{ color }}>
          Kill-switch: {blocked ? 'HALTED' : 'clear'}
        </span>
        {blocked && (
          <span className="text-xs font-mono text-[var(--color-text-muted)]">
            {layer ? `[${layer}] ` : ''}{reason}
          </span>
        )}
      </div>
    </div>
  )
}

function DeployedTable({ rows }: { rows: LiveDeployed[] }) {
  if (!rows.length) {
    return (
      <div className="text-sm text-[var(--color-text-muted)] py-6 text-center">
        No strategies deployed. The forge promotes a PASS into shadow here; real capital is gated on
        forward-paper evidence + the AUM floor (board 2026-06-09).
      </div>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead className="text-[var(--color-text-muted)] uppercase tracking-wider text-[10px]">
          <tr className="border-b border-[var(--color-border)]">
            <th className="text-left py-2 pr-4">Strategy</th>
            <th className="text-left py-2 pr-4">State</th>
            <th className="text-left py-2 pr-4">Broker</th>
            <th className="text-right py-2 pr-4">Capital</th>
            <th className="text-left py-2 pr-4">Approved</th>
            <th className="text-right py-2">Exp. Sharpe</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.name} className="border-b border-[var(--color-border)]/40">
              <td className="py-2 pr-4 text-[var(--color-text)]">{s.name}</td>
              <td className="py-2 pr-4" style={{ color: STATE_COLOR[s.state] ?? 'var(--color-text)' }}>{s.state}</td>
              <td className="py-2 pr-4 text-[var(--color-text-muted)]">{s.broker}</td>
              <td className="py-2 pr-4 text-right tabular-nums">${s.capital.toLocaleString()}</td>
              <td className="py-2 pr-4">{s.approved ? '✅' : '—'}</td>
              <td className="py-2 text-right tabular-nums">{s.expectation?.sharpe?.toFixed(2) ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DailyResults({ results }: { results: LiveDailyResult[] }) {
  if (!results.length) {
    return <div className="text-sm text-[var(--color-text-muted)] py-4 text-center">No runs in the latest report.</div>
  }
  return (
    <div className="space-y-2">
      {results.map((r) => {
        const tag = r.blocked ? '⛔' : r.track_status === 'diverging' ? '⚠️' : '✅'
        return (
          <div key={r.name} className="flex items-center justify-between gap-3 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-xs font-mono">
            <span className="text-[var(--color-text)]">{tag} {r.name}</span>
            <span className="text-[var(--color-text-muted)]">
              [{r.state}/{r.broker}] orders={r.n_orders} exec={r.executed} dry={String(r.dry_run)} track={r.track_status ?? '—'}
              {r.awaiting_approval ? '  🟡 AWAITING APPROVAL' : ''}
              {r.error ? `  err=${r.error}` : ''}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export function LiveTab() {
  const { data } = useLiveState()
  if (!data) return <Skeleton className="h-64" />

  return (
    <div className="space-y-4 md:space-y-6 stagger">
      <div className="animate-in">
        <KillSwitchBanner {...data.kill_switch} />
      </div>
      <div className="animate-in">
        <SectionBoundary title="Deployed Strategies">
          <DeployedTable rows={data.deployed} />
        </SectionBoundary>
      </div>
      <div className="animate-in">
        <SectionBoundary title={`Latest Shadow Run${data.daily ? ` · ${data.daily.date} (${data.daily.mode})` : ''}`}>
          {data.daily ? <DailyResults results={data.daily.results} /> : (
            <div className="text-sm text-[var(--color-text-muted)] py-4 text-center">No daily report yet.</div>
          )}
        </SectionBoundary>
      </div>
    </div>
  )
}
