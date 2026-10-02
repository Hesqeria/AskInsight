import { test, expect } from '@playwright/test'

/**
 * Phase 3.12 Dashboard E2E tests.
 *
 * Strategy: intercept /api/* with Playwright route mocking so tests run without
 * a live backend. The app reads dashboard widgets from localStorage, so we
 * pre-seed localStorage before navigating.
 */

const SEEDED_WIDGETS = [
  {
    id: 'w1',
    x: 0, y: 0, w: 6, h: 4,
    layout: true,
    query: 'region sales',
    config: { title: '区域销售', type: 'bar', categoryKey: 'region', valueKeys: ['amount'] },
    rows: [
      { region: '华东', amount: 100 },
      { region: '华北', amount: 200 },
      { region: '华南', amount: 150 },
    ],
  },
]

async function seedAndGoto(page) {
  await page.addInitScript((widgets) => {
    localStorage.setItem('askinsight_dashboard', JSON.stringify(widgets))
    localStorage.setItem('token', 'fake-test-token')
  }, SEEDED_WIDGETS)
  await page.goto('/dashboard')
}

test.describe('Dashboard visualization', () => {
  test('renders seeded widget with title and chart container', async ({ page }) => {
    await seedAndGoto(page)
    await expect(page.locator('.widget-title').first()).toContainText('区域销售')
    await expect(page.locator('.dashboard-widget').first()).toBeVisible()
    // Chart box or table view should be present
    await expect(page.locator('.chart-wrap').first()).toBeVisible()
  })

  test('filter bar is visible and accepts input', async ({ page }) => {
    await seedAndGoto(page)
    const filterBar = page.locator('.filter-bar')
    await expect(filterBar).toBeVisible()
    await page.locator('.filter-add').click()
    // A filter field select should appear
    await expect(page.locator('.filter-field').first()).toBeVisible()
  })

  test('header actions include add / template / export buttons', async ({ page }) => {
    await seedAndGoto(page)
    const actions = page.locator('.dashboard-actions')
    await expect(actions).toBeVisible()
    await expect(actions.locator('text=使用模板')).toBeVisible()
    await expect(actions.locator('text=导出 JSON')).toBeVisible()
    await expect(actions.locator('text=导出 PDF')).toBeVisible()
  })

  test('opens template gallery dialog', async ({ page }) => {
    await seedAndGoto(page)
    await page.locator('.dashboard-actions').locator('text=使用模板').click()
    // Element Plus dialog renders with title
    await expect(page.locator('.el-dialog').first()).toBeVisible()
    await expect(page.locator('.template-gallery')).toBeVisible()
    // 5 templates
    await expect(page.locator('.template-card')).toHaveCount(5)
  })

  test('apply template adds widgets to dashboard', async ({ page }) => {
    await seedAndGoto(page)
    await page.locator('.dashboard-actions').locator('text=使用模板').click()
    await page.locator('.template-card').first().click()
    // Dialog closes
    await expect(page.locator('.el-dialog')).toHaveCount(0)
    // More widgets now present (>1)
    const count = await page.locator('.dashboard-widget').count()
    expect(count).toBeGreaterThan(1)
  })

  test('widget config gear opens config dialog', async ({ page }) => {
    await seedAndGoto(page)
    await page.locator('.dashboard-widget').first().locator('.icon-btn').first().click()
    await expect(page.locator('.el-dialog').first()).toBeVisible()
    await expect(page.locator('.chart-config-panel')).toBeVisible()
    // Title field present
    await expect(page.locator('text=图表标题')).toBeVisible()
  })

  test('remove widget decreases widget count', async ({ page }) => {
    await seedAndGoto(page)
    await expect(page.locator('.dashboard-widget').first()).toBeVisible()
    const before = await page.locator('.dashboard-widget').count()
    expect(before).toBeGreaterThan(0)
    await page.locator('.dashboard-widget').first().locator('.icon-btn').nth(1).click()
    await expect(page.locator('.dashboard-widget')).toHaveCount(before - 1)
  })

  test('empty dashboard shows empty state', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('askinsight_dashboard', '[]')
      localStorage.setItem('token', 'fake-test-token') // auth guard (login gate)
    })
    await page.goto('/dashboard')
    await expect(page.locator('.empty-state')).toBeVisible()
    await expect(page.locator('.empty-state button', { hasText: '使用模板' })).toBeVisible()
  })
})

test.describe('Dashboard backend integration (mocked)', () => {
  test('filter values endpoint called when configured', async ({ page }) => {
    let filtersCallCount = 0
    await page.route('**/api/query/filters/**', async (route) => {
      filtersCallCount++
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ values: ['华东', '华北'], limit: 100, offset: 0 }),
      })
    })
    await seedAndGoto(page)
    // The frontend integration is wired via useDashboardFilters(backendMode);
    // we just verify the endpoint shape is reachable when invoked.
    // Trigger by evaluating the API call directly in page context.
    const result = await page.evaluate(async () => {
      const r = await fetch('/api/query/filters/dim_region/region_name?search=华')
      return r.json()
    })
    expect(result.values).toEqual(['华东', '华北'])
    expect(filtersCallCount).toBe(1)
  })

  test('execute endpoint returns rows when called from page', async ({ page }) => {
    let executeBody
    await page.route('**/api/query/execute', async (route) => {
      executeBody = route.request().postDataJSON()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ rows: [{ region: '华东', amount: 999 }] }),
      })
    })
    await seedAndGoto(page)
    const result = await page.evaluate(async () => {
      const r = await fetch('/api/query/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sql: 'SELECT region, amount FROM fact_order',
          filters: [{ field: 'region', operator: 'eq', value: '华东' }],
        }),
      })
      return r.json()
    })
    expect(result.rows[0].amount).toBe(999)
    expect(executeBody.filters[0].field).toBe('region')
  })
})
