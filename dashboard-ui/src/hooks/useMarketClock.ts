import { useEffect, useState } from 'react'
import type { MarketClock } from '../api/types'

export function useMarketClock(clock: MarketClock | undefined | null): string {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  if (!clock) return '\u2014'

  const isOpen = clock.is_open === true
  const target = isOpen ? clock.next_close : clock.next_open
  if (!target) {
    return isOpen ? 'Market open' : 'Market closed'
  }

  const targetDate = new Date(target)
  if (Number.isNaN(targetDate.getTime())) {
    return isOpen ? 'Market open' : 'Market closed'
  }

  const diffMs = targetDate.getTime() - now.getTime()
  if (diffMs <= 0) {
    return isOpen ? 'Closing\u2026' : 'Opening\u2026'
  }

  const totalSec = Math.floor(diffMs / 1000)
  const hours = Math.floor(totalSec / 3600)
  const minutes = Math.floor((totalSec % 3600) / 60)
  const seconds = totalSec % 60

  const pad = (n: number) => n.toString().padStart(2, '0')
  const label = isOpen ? 'Closes in' : 'Opens in'
  if (hours > 0) {
    return `${label} ${hours}:${pad(minutes)}:${pad(seconds)}`
  }
  return `${label} ${pad(minutes)}:${pad(seconds)}`
}
