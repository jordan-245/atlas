import { useLiveState } from '../../api/queries'
import type { LiveDeployed, LiveDailyResult, LivePortfolio } from '../../api/queries'
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

function pnlColor(v?: number | null): string {
  if (v == null) return 'var(--color-text-muted)'
  return v >= 0 ? 'var(--color-green)' : 'var(--color-red)'
}

function fmtPct(v?: number | null): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
}

function Sparkline({ curve, base }: { curve: { equity?: number }[]; base?: number | null }) {
  const pts = curve.map((c) => c.equity).filter((e): e is number => e != null)
  if (pts.length < 2) return <span className="text-[10px] text-[var(--color-text-muted)]">—</span>
  const w = 96
  const h = 24
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const span = max - min || 1
  const path = pts
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${((i / (pts.length - 1)) * w).toFixed(1)},${(h - ((p - min) / span) * h).toFixed(1)}`)
    .join(' ')
  const up = base != null ? pts[pts.length - 1] >= base : pts[pts.length - 1] >= pts[0]
  return (
    <svg width={w} height={h} className="inline-block align-middle">
      <path d={path} fill="none" stroke={up ? 'var(--color-green)' : 'var(--color-red)'} strokeWidth="1.5" />
    </svg>
  )
}

function PortfolioHeader({ p }: { p: LivePortfolio }) {
  const items: [string, string, string][] = [
    ['Book Equity', `$${p.total_equity.toLocaleString()}`, 'var(--color-text)'],
    ['Capital Base', `$${p.total_capital_base.toLocaleString()}`, 'var(--color-text-muted)'],
    ['P&L', `${p.total_pnl >= 0 ? '+' : ''}$${p.total_pnl.toLocaleString()}`, pnlColor(p.total_pnl)],
    ['Return', fmtPct(p.total_return), pnlColor(p.total_return)],
    ['Strategies', `${p.n_tracked}/${p.n_strategies} tracked`, 'var(--color-text-muted)'],
  ]
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {items.map(([label, value, color]) => (
        <div key={label} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{label}</div>
          <div className="text-sm font-mono font-semibold tabular-nums" style={{ color }}>{value}</div>
        </div>
      ))}
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
            <th className="text-right py-2 pr-4">Equity</th>
            <th className="text-right py-2 pr-4">Cum Ret</th>
            <th className="text-right py-2 pr-4">Last Day</th>
            <th className="text-right py-2 pr-4">Days</th>
            <th className="text-right py-2 pr-4">Pos</th>
            <th className="text-right py-2 pr-4">Sharpe R/E</th>
            <th className="text-left py-2 pr-4">Curve</th>
            <th className="text-left py-2">Appr</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const b = s.book
            return (
              <tr key={s.name} className="border-b border-[var(--color-border)]/40">
                <td className="py-2 pr-4 text-[var(--color-text)]">
                  {s.name}
                  <span className="ml-2 text-[10px]" style={{ color: STATE_COLOR[s.state] ?? 'var(--color-text-muted)' }}>
                    {s.state}/{s.broker}
                  </span>
                </td>
                <td className="py-2 pr-4" style={{ color: STATE_COLOR[s.state] ?? 'var(--color-text)' }}>{s.state}</td>
                <td className="py-2 pr-4 text-right tabular-nums">
                  {b?.book_equity != null ? `$${b.book_equity.toLocaleString()}` : `$${s.capital.toLocaleString()}`}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums" style={{ color: pnlColor(b?.cum_return) }}>
                  {fmtPct(b?.cum_return)}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums" style={{ color: pnlColor(b?.last_return) }}>
                  {fmtPct(b?.last_return)}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums text-[var(--color-text-muted)]">{b?.days_tracked ?? 0}</td>
                <td className="py-2 pr-4 text-right tabular-nums text-[var(--color-text-muted)]">{b?.n_positions ?? '—'}</td>
                <td className="py-2 pr-4 text-right tabular-nums">
                  {b?.realized_sharpe != null ? b.realized_sharpe.toFixed(2) : '—'}
                  <span className="text-[var(--color-text-muted)]"> / {s.expectation?.sharpe?.toFixed(2) ?? '—'}</span>
                </td>
                <td className="py-2 pr-4"><Sparkline curve={b?.equity_curve ?? []} base={b?.capital_base} /></td>
                <td className="py-2">{s.approved ? '✅' : '—'}</td>
              </tr>
            )
          })}
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
      {data.portfolio && (
        <div className="animate-in">
          <SectionBoundary title="Paper Portfolio">
            <PortfolioHeader p={data.portfolio} />
          </SectionBoundary>
        </div>
      )}
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
