/**
 * Playwright tests for the Strategy Lifecycle UI (Controls tab).
 *
 * NOTE: Playwright is not currently installed in this project.
 * Install with: npm install -D @playwright/test && npx playwright install chromium
 * Then run with: VITE_ENABLE_CONTROLS_TAB=true npx playwright test lifecycle-controls.spec.ts
 *
 * All tests mock the backend via page.route() so no live backend is required.
 */

import { test, expect } from '@playwright/test'

// ── Helpers ──────────────────────────────────────────────────────────

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173'

/** Mocked lifecycle rows, one per state */
const MOCK_ROWS = [
  {
    strategy: 'momentum', universe: 'ASX200', state: 'RESEARCH',
    entered_state_at: '2026-01-01T00:00:00Z', prev_state: null, transition_reason: null,
    paper_start_date: null, paper_end_date: null,
    research_sharpe: 1.2, paper_sharpe: null, paper_trades_count: null,
    days_in_paper: null, gap: null, live_sharpe: null, live_trades_count: null,
  },
  {
    strategy: 'mean_rev', universe: 'ASX200', state: 'PAPER',
    entered_state_at: '2026-02-01T00:00:00Z', prev_state: 'RESEARCH', transition_reason: 'Looks good',
    paper_start_date: '2026-02-01', paper_end_date: null,
    research_sharpe: 1.5, paper_sharpe: 1.21, paper_trades_count: 42,
    days_in_paper: 28, gap: 0.29, live_sharpe: null, live_trades_count: null,
  },
  {
    strategy: 'trend', universe: 'ASX200', state: 'LIVE',
    entered_state_at: '2026-03-01T00:00:00Z', prev_state: 'PAPER', transition_reason: 'All gates passed',
    paper_start_date: '2026-01-01', paper_end_date: '2026-03-01',
    research_sharpe: 1.8, paper_sharpe: 1.65, paper_trades_count: 80,
    days_in_paper: 59, gap: 0.15, live_sharpe: 1.70, live_trades_count: 20,
  },
  {
    strategy: 'vol_arb', universe: 'ASX200', state: 'RETIRED',
    entered_state_at: '2026-04-01T00:00:00Z', prev_state: 'LIVE', transition_reason: 'Underperforming',
    paper_start_date: null, paper_end_date: null,
    research_sharpe: 0.8, paper_sharpe: 0.7, paper_trades_count: 30,
    days_in_paper: 40, gap: 0.1, live_sharpe: 0.5, live_trades_count: 10,
  },
]

const MOCK_STRATEGIES = MOCK_ROWS.map((lr) => ({
  market_id: lr.universe,
  strategy: lr.strategy,
  effective_enabled: true,
  config_enabled: true,
  weight: 0.25,
  override: null,
  open_positions: 0,
  trades_30d: 10,
  pnl_30d: 500,
  lifecycle: 'ACTIVE',
}))

/** Mount page, navigate to Controls tab, and wait for strategies section */
async function gotoControls(page: import('@playwright/test').Page) {
  // Mock admin endpoints
  await page.route('**/api/admin/universes', (route) =>
    route.fulfill({ json: { universes: [] } }),
  )
  await page.route('**/api/admin/strategies', (route) =>
    route.fulfill({ json: { strategies: MOCK_STRATEGIES } }),
  )
  await page.route('**/api/strategy-lifecycle', (route) =>
    route.fulfill({ json: { rows: MOCK_ROWS } }),
  )
  await page.route('**/api/admin/override-audit**', (route) =>
    route.fulfill({ json: { audit: [], next_cursor: null } }),
  )
  await page.route('**/api/strategy-lifecycle/recent-history**', (route) =>
    route.fulfill({ json: { history: [] } }),
  )

  await page.goto(`${BASE_URL}/?tab=controls`)
  // If app doesn't support ?tab param, click the Controls tab
  const controlsTab = page.locator('[data-tab="controls"], button:has-text("Controls")')
  if (await controlsTab.count() > 0) {
    await controlsTab.first().click()
  }
}

// ── Tests ─────────────────────────────────────────────────────────────

test.describe('Strategy Lifecycle UI', () => {

  test('1. lifecycle badge renders correctly per state', async ({ page }) => {
    await gotoControls(page)

    // Expand ASX200 universe group if collapsed
    const groupBtn = page.locator('button:has-text("ASX200")')
    if (await groupBtn.count() > 0) await groupBtn.first().click()

    const badges = page.locator('[data-testid="lifecycle-state-badge"]')
    await expect(badges).toHaveCount(4)

    // RESEARCH badge should be blue-ish
    const researchBadge = badges.filter({ hasText: 'RESEARCH' })
    await expect(researchBadge).toBeVisible()
    await expect(researchBadge).toHaveClass(/text-blue-400/)

    // PAPER badge should be amber-ish
    const paperBadge = badges.filter({ hasText: 'PAPER' })
    await expect(paperBadge).toBeVisible()
    await expect(paperBadge).toHaveClass(/text-amber-400/)

    // LIVE badge should be green
    const liveBadge = badges.filter({ hasText: 'LIVE' })
    await expect(liveBadge).toBeVisible()
    await expect(liveBadge).toHaveClass(/text-green-400/)

    // RETIRED badge should be grey
    const retiredBadge = badges.filter({ hasText: 'RETIRED' })
    await expect(retiredBadge).toBeVisible()
    await expect(retiredBadge).toHaveClass(/text-zinc-400/)
  })

  test('2. clicking promote-paper opens modal and submits transition', async ({ page }) => {
    let capturedBody: unknown = null

    await page.route('**/api/strategy-lifecycle/transition', async (route) => {
      capturedBody = JSON.parse(route.request().postData() ?? '{}')
      await route.fulfill({ json: { transitioned: true } })
    })

    await gotoControls(page)

    // Expand universe group
    const groupBtn = page.locator('button:has-text("ASX200")')
    if (await groupBtn.count() > 0) await groupBtn.first().click()

    // Click "Promote to PAPER" for the RESEARCH strategy
    const promoteBtn = page.locator('[data-testid="action-promote-paper"]').first()
    await promoteBtn.click()

    // Modal should be visible
    const modal = page.locator('[data-testid="lifecycle-submit-btn"]')
    await expect(modal).toBeVisible()

    // Fill reason
    await page.locator('textarea').fill('Promoting because research sharpe looks solid')

    // Submit
    await modal.click()

    // Verify POST body
    await page.waitForTimeout(500)
    expect(capturedBody).toMatchObject({
      strategy:  'momentum',
      universe:  'ASX200',
      new_state: 'PAPER',
    })
  })

  test('3. disallowed transition shows force-override option', async ({ page }) => {
    // First call returns 400 Disallowed; second (with force=true) succeeds
    let callCount = 0
    await page.route('**/api/strategy-lifecycle/transition', async (route) => {
      callCount++
      if (callCount === 1) {
        await route.fulfill({
          status: 400,
          json: { detail: 'Disallowed system transition: LIVE → RESEARCH' },
        })
      } else {
        await route.fulfill({ json: { transitioned: true } })
      }
    })

    await gotoControls(page)

    // Click rollback_paper for the LIVE strategy
    const groupBtn = page.locator('button:has-text("ASX200")')
    if (await groupBtn.count() > 0) await groupBtn.first().click()

    const rollbackBtn = page.locator('[data-testid="action-rollback-paper"]').first()
    await rollbackBtn.click()

    // Fill reason and submit (triggers 400)
    await page.locator('textarea').fill('Rolling back because paper data diverged')
    await page.locator('[data-testid="lifecycle-submit-btn"]').click()

    // Force-override section should appear
    const forceSection = page.locator('[data-testid="force-override-section"]')
    await expect(forceSection).toBeVisible()
    await expect(forceSection).toContainText('Disallowed')

    // Check override checkbox
    await page.locator('[data-testid="force-override-checkbox"]').check()

    // Resubmit
    await page.locator('[data-testid="lifecycle-submit-btn"]').click()
    await page.waitForTimeout(300)

    // Ensure we made 2 calls total
    expect(callCount).toBe(2)
  })

  test('4. lifecycle history modal renders timeline', async ({ page }) => {
    const mockHistory = [
      {
        from_state: 'RESEARCH', to_state: 'PAPER',
        transitioned_at: '2026-02-01T00:00:00Z',
        reason: 'Initial paper promotion', operator: 'admin', auto_promotion_id: null,
      },
      {
        from_state: null, to_state: 'RESEARCH',
        transitioned_at: '2026-01-01T00:00:00Z',
        reason: 'Strategy created', operator: 'system', auto_promotion_id: null,
      },
    ]

    await page.route('**/api/strategy-lifecycle/momentum/ASX200/history', (route) =>
      route.fulfill({ json: { history: mockHistory } }),
    )

    await gotoControls(page)

    const groupBtn = page.locator('button:has-text("ASX200")')
    if (await groupBtn.count() > 0) await groupBtn.first().click()

    // Click the RESEARCH badge for 'momentum'
    const badge = page.locator('[data-testid="lifecycle-state-badge"]').filter({ hasText: 'RESEARCH' }).first()
    await badge.click()

    // History modal should open and show timeline items
    const toStates = page.locator('[data-testid="history-to-state"]')
    await expect(toStates).toHaveCount(2)
    await expect(toStates.first()).toContainText('PAPER')
    await expect(toStates.last()).toContainText('RESEARCH')
  })

})
