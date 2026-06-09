import { usePortfolioData } from '../../api/queries'
import { useMarketClock } from '../../hooks/useMarketClock'
import { useTheme } from '../../hooks/useTheme'
import { DataFreshnessChip } from './DataFreshnessChip'
import { StatusDot } from '../shared/StatusDot'

export function Header() {
  const { data: portfolioData } = usePortfolioData()
  const { toggleTheme } = useTheme()
  const clockString = useMarketClock(portfolioData?.market_clock)
  const isOpen = portfolioData?.market_clock?.is_open === true

  return (
    <header className="sticky top-0 z-40 h-14 bg-[var(--color-surface)]/80 backdrop-blur-md border-b border-[var(--color-border)] shadow-sm">
      <div className="max-w-[1440px] mx-auto h-full px-6 flex items-center gap-3 md:gap-4">

        {/* Logo */}
        <div className="font-mono font-semibold text-base tracking-[-0.03em] select-none flex items-center gap-1">
          ▲ Atlas
          <span className="w-1 h-1 rounded-full bg-[var(--color-accent)] opacity-60 ml-0.5" aria-hidden="true" />
        </div>

        {/* Market Clock */}
        <span className="text-xs text-[var(--color-text)] font-mono tabular-nums flex items-center gap-1.5">
          <StatusDot status={isOpen ? 'green' : 'gray'} size="sm" pulse={isOpen} />
          {clockString}
        </span>

        <div className="flex-1" />

        <DataFreshnessChip />

        <a
          href="/homerbot"
          className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors inline-flex items-center justify-center w-9 h-9 rounded-lg hover:bg-[var(--color-surface-alt)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border)]"
          aria-label="Homerbot"
          title="Homerbot"
        >
          ◈
        </a>

        <button
          onClick={toggleTheme}
          className="text-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors w-9 h-9 flex items-center justify-center rounded-lg hover:bg-[var(--color-surface-alt)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border)]"
          aria-label="Toggle theme"
        >
          ◑
        </button>
      </div>
    </header>
  )
}
