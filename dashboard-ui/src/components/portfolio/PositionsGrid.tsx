import type { Position } from '../../api/types'
import { PositionCard } from './PositionCard'
import { EmptyState } from '../shared/EmptyState'

interface Props { positions: Position[] }

export function PositionsGrid({ positions }: Props) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium mb-3">OPEN POSITIONS ({positions.length})</div>
      {positions.length === 0 ? (
        <EmptyState message="No open positions" />
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
          {positions.map((p, i) => <PositionCard key={p.ticker ?? i} position={p} />)}
        </div>
      )}
    </div>
  )
}
