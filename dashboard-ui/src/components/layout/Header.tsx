import { useRegimeCurrent, usePortfolioData } from '../../api/queries'
import { useMarketClock } from '../../hooks/useMarketClock'
import { useTheme } from '../../hooks/useTheme'
import { getRegimeColor } from '../../lib/colors'

export function Header() {
  const { data: regimeData, isLoading: regimeLoading } = useRegimeCurrent()
  const { data: portfolioData } = usePortfolioData()
  const { toggleTheme } = useTheme()
  const clockString = useMarketClock(portfolioData?.market_clock)

  const regimeState = regimeData?.label || regimeData?.state || '—'
  const regimeColor = getRegimeColor(regimeData?.state)

  return (
    <header className="sticky top-0 z-40 h-14 bg-[var(--color-surface)]/80 backdrop-blur-md border-b border-[var(--color-border)] shadow-sm">
      <div className="max-w-[1440px] mx-auto h-full px-6 flex items-center gap-3 md:gap-4">
        {/* Logo */}
        <div className="font-semibold text-lg tracking-tight">▲ Atlas</div>

        {/* Regime Badge */}
        {regimeLoading ? (
          <div className="h-6 w-24 rounded-full bg-[var(--color-surface-alt)] animate-pulse" />
        ) : (
          <div
            className="flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium border"
            style={{
              backgroundColor: regimeColor + '18',
              borderColor: regimeColor + '40',
              color: regimeColor,
            }}
          >
            <span
              className="w-1.5 h-1.5 rounded-full"
              style={{ backgroundColor: regimeColor }}
            />
            {regimeState}
          </div>
        )}

        {/* Dynamic Sizing label */}
        <span className="hidden md:inline text-[11px] text-[var(--color-text-muted)] font-mono">Dynamic sizing</span>

        {/* Market Clock */}
        <span className="text-xs text-[var(--color-text-muted)] font-mono tabular-nums">{clockString}</span>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Agent Link */}
        <a
          href="/homerbot"
          className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors inline-flex items-center gap-1.5 min-h-[44px]"
        >
          ◈ <span className="hidden md:inline">Homerbot</span>
        </a>

        {/* Theme Toggle */}
        <button
          onClick={toggleTheme}
          className="text-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg hover:bg-[var(--color-surface-alt)]"
          aria-label="Toggle theme"
        >
          ◑
        </button>
      </div>
    </header>
  )
}
