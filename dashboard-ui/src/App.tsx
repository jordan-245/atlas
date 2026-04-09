import { lazy, Suspense, useState } from 'react'
import { useTheme } from './hooks/useTheme'
import { Header } from './components/layout/Header'
import { TabBar } from './components/layout/TabBar'
import { PortfolioTab } from './components/portfolio/PortfolioTab'
import { ErrorBoundary } from './components/layout/ErrorBoundary'

// Rule: bundle-dynamic-imports — lazy-load FinanceTab so the recharts chunk is
// NOT bundled into the main chunk. The Suspense fallback shows an animated
// skeleton that matches the tab content height to prevent layout shift
// (async-suspense-boundaries rule: show wrapper UI faster while data/code loads).
const FinanceTab = lazy(() =>
  import('./components/finance/FinanceTab').then((m) => ({ default: m.FinanceTab }))
)

// TODO: preload on TabBar hover — import { preloadFinanceTab } from '../../App'
// and call it in TabBar's onMouseEnter for the Finance button so the network
// request starts before the user clicks (bundle-conditional rule).
export const preloadFinanceTab = () => import('./components/finance/FinanceTab')

export default function App() {
  useTheme()
  const [activeTab, setActiveTab] = useState<'portfolio' | 'finance'>('portfolio')

  return (
    <div className="min-h-screen bg-[var(--color-bg)] text-[var(--color-text)]">
      <Header />
      <div className="max-w-[1440px] mx-auto px-6">
        <TabBar activeTab={activeTab} onChange={setActiveTab} />
        <main className="py-6">
          <ErrorBoundary>
            {activeTab === 'portfolio' ? (
              <PortfolioTab />
            ) : (
              <Suspense
                fallback={
                  <div className="h-96 animate-pulse bg-[var(--color-surface)] rounded-xl" />
                }
              >
                <FinanceTab />
              </Suspense>
            )}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
