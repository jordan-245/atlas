import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import type { PacePoint } from '../../api/types'
import { fmtSignedCcy, fmtDateShort } from '../../lib/format'

interface Props {
  paceData: PacePoint[]
  paceStatus?: string
  paceDiff?: number
}

function badgeClass(status: string | undefined): string {
  if (status === 'under') return 'bg-[var(--color-green)]/20 text-[var(--color-green)]'
  if (status === 'over') return 'bg-[var(--color-red)]/20 text-[var(--color-red)]'
  return 'bg-[var(--color-surface-alt)] text-[var(--color-text-muted)]'
}

export function SpendingPaceChart({ paceData, paceStatus, paceDiff }: Props) {
  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">SPENDING PACE</div>
        {paceStatus != null ? (
          <div className={`rounded-full px-3 py-1 text-[10px] font-mono font-medium uppercase ${badgeClass(paceStatus)}`}>
            {paceStatus} {paceDiff != null ? fmtSignedCcy(paceDiff) : null}
          </div>
        ) : null}
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={paceData}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={(v) => fmtDateShort(v as string)}
            axisLine={false}
            tickLine={false}
            tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }}
          />
          <YAxis
            tickFormatter={(v) => '$' + Math.round(v as number).toLocaleString('en-US')}
            axisLine={false}
            tickLine={false}
            tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }}
          />
          <Tooltip />
          <Legend />
          <Line dataKey="actual" name="Actual" stroke="#22c55e" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="budget" name="Budget" stroke="#a1a1aa" strokeDasharray="4 4" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
