import type { SystemHealth as SystemHealthData, HealthCronJob, HealthDataFreshness } from '../../api/types'
import { EmptyState } from '../shared/EmptyState'
import { fmtRelativeTime } from '../../lib/format'

interface Props { data: SystemHealthData }

function overallBadge(overall?: string) {
  const s = (overall ?? '').toLowerCase()
  let cls: string
  if (s === 'healthy' || s === 'ok') {
    cls = 'bg-[var(--color-green)]/20 text-[var(--color-green)]'
  } else if (s === 'degraded') {
    cls = 'bg-[#f59e0b]/20 text-[#f59e0b]'
  } else if (s === 'down' || s === 'error') {
    cls = 'bg-[var(--color-red)]/20 text-[var(--color-red)]'
  } else {
    cls = 'bg-[var(--color-surface-alt)] text-[var(--color-text-muted)]'
  }
  return (
    <span className={`rounded-md px-2 py-0.5 text-[10px] font-mono ${cls}`}>
      {(overall ?? '').toUpperCase() || '\u2014'}
    </span>
  )
}

function statusDot(status?: string) {
  const s = (status ?? '').toLowerCase()
  let color: string
  if (s === 'active' || s === 'ok' || s === 'healthy' || s === 'running') {
    color = 'var(--color-green)'
  } else if (s === 'degraded' || s === 'warning') {
    color = '#f59e0b'
  } else {
    color = 'var(--color-red)'
  }
  return (
    <span
      className="inline-block rounded-full shrink-0 mt-0.5"
      style={{ width: 8, height: 8, backgroundColor: color }}
    />
  )
}

function ServicesList({ services }: { services: Record<string, string> }) {
  const entries = Object.entries(services)
  if (entries.length === 0) return <EmptyState message="No data" className="py-4" />
  return (
    <div className="space-y-2">
      {entries.map(([name, status]) => (
        <div key={name}>
          <div className="flex items-start gap-2">
            {statusDot(status)}
            <div className="min-w-0">
              <span className="text-sm">{name}</span>
              <span className="text-xs text-[var(--color-text-muted)] ml-2 font-mono">{status}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function CronJobsList({ jobs }: { jobs: Record<string, HealthCronJob> }) {
  const entries = Object.entries(jobs)
  if (entries.length === 0) return <EmptyState message="No data" className="py-4" />
  return (
    <div className="space-y-2">
      {entries.map(([name, j]) => {
        const failed = j.exit_code != null && j.exit_code !== 0
        const s = (j.status ?? '').toLowerCase()
        let cls: string
        if (failed) {
          cls = 'bg-[var(--color-red)]/20 text-[var(--color-red)]'
        } else if (s === 'ok' || s === 'success' || s === 'completed') {
          cls = 'bg-[var(--color-green)]/20 text-[var(--color-green)]'
        } else {
          cls = 'bg-[var(--color-surface-alt)] text-[var(--color-text-muted)]'
        }
        const label = (j.status ?? '').toUpperCase() || (failed ? 'FAIL' : 'OK')
        return (
          <div key={name} className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="text-sm truncate">{name}</div>
              <div className="text-xs text-[var(--color-text-muted)] font-mono">
                {fmtRelativeTime(j.last_run)}
              </div>
            </div>
            <span className={`rounded-md px-2 py-0.5 text-[10px] font-mono shrink-0 ${cls}`}>
              {label}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function DataFreshnessList({ data }: { data: HealthDataFreshness }) {
  const items: { label: string; value: string }[] = []
  if (data.ohlcv_last_date) items.push({ label: 'OHLCV last', value: data.ohlcv_last_date })
  if (data.equity_last_date) items.push({ label: 'Equity last', value: data.equity_last_date })
  if (data.overlay_decisions_count != null) items.push({ label: 'Overlay decisions', value: String(data.overlay_decisions_count) })
  if (items.length === 0) return <EmptyState message="No data" className="py-4" />
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.label} className="flex items-center justify-between gap-2">
          <div className="text-sm truncate">{item.label}</div>
          <span className="text-xs font-mono shrink-0 text-[var(--color-text-muted)]">{item.value}</span>
        </div>
      ))}
    </div>
  )
}

export function SystemHealth({ data }: Props) {
  const services = data.services ?? {}
  const cronJobs = data.cron ?? {}
  const freshness = data.data_freshness ?? {}
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
          SYSTEM HEALTH
        </div>
        {overallBadge(data.overall)}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <div className="text-[9px] uppercase tracking-wider text-[var(--color-text-muted)] mb-2">SERVICES</div>
          <ServicesList services={services} />
        </div>
        <div>
          <div className="text-[9px] uppercase tracking-wider text-[var(--color-text-muted)] mb-2">CRON JOBS</div>
          <CronJobsList jobs={cronJobs} />
        </div>
        <div>
          <div className="text-[9px] uppercase tracking-wider text-[var(--color-text-muted)] mb-2">DATA FRESHNESS</div>
          <DataFreshnessList data={freshness} />
        </div>
      </div>
    </div>
  )
}
