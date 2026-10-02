import { test, expect } from '@playwright/test'

/**
 * Multi-path candidate E2E tests (多路重排).
 *
 * The SSE `clarify_required` event carries `candidates` — a ranked list
 * of complete plans. The UI should render the candidate list, let the
 * user pick one, and POST to resume with response_type=candidate.
 */

const CANDIDATE_SSE = [
  'data: {"stage":"Intent Recognition"}\n\n',
  'data: {"stage":"Semantic Grounding"}\n\n',
  'data: {"stage":"Pending Clarification"}\n\n',
  'data: {"clarify_required":{"clarify_id":"clr_cand_1","question":"看下销售",'
    + '"missing_fields":[{"field":"measure","reason":"missing"}],'
    + '"suggestions":[{"field":"measure","prompt":"您想看哪个指标?","multi_select":false,'
    + '"allow_custom":true,"options":[{"label":"GMV","value":"GMV"},'
    + '{"label":"订单数","value":"order_count"}]}],'
    + '"candidates":['
    + '{"_candidate_id":"cand_1","confidence":1.0,"_score":0.95,'
    + '"_explain":"查询订单的总成交金额(GMV)总和,时间范围 2026-07-01 至 2026-07-31",'
    + '"_signals":{"confidence":1.0,"rerank":0.9,"history":1.0},'
    + '"sql_preview":"SELECT SUM(t.total_amount) FROM dw.dwd_order_info_inc AS t WHERE t.order_status = \'PAID\'"},'
    + '{"_candidate_id":"cand_2","confidence":0.9,"_score":0.80,'
    + '"_explain":"查询订单的订单数,时间范围 2026-07-01 至 2026-07-31",'
    + '"_signals":{"confidence":0.9,"rerank":0.7,"history":0.0},'
    + '"sql_preview":"SELECT COUNT(t.id) FROM dw.dwd_order_info_inc AS t"}'
    + '],'
    + '"current_summary":"未识别到明确的指标,请补充",'
    + '"resume_url":"/api/v1/clarify/clr_cand_1/resume",'
    + '"inbox_id":"ibx_cand_1","respond_url":"/api/v1/inbox/ibx_cand_1/respond","status":"pending"}}\n\n',
].join('')

test.describe('Multi-path candidate flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('token', 'fake-test-token')
    })
  })

  test('renders candidate list with ranked plans', async ({ page }) => {
    await page.route('**/api/query', async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: CANDIDATE_SSE,
      })
    })
    await page.goto('/')
    await page.locator('textarea').fill('看下销售')
    await page.keyboard.press('Enter')

    const list = page.locator('.candidate-list')
    await expect(list).toBeVisible()
    // Both candidate cards render.
    await expect(list.locator('.candidate-card')).toHaveCount(2)
    // Best tag on first.
    await expect(list.locator('.best-tag')).toHaveCount(1)
    // Explanations visible.
    await expect(list.locator('.cand-explain').first()).toContainText('总成交金额')
  })

  test('selecting a candidate and executing calls resume with candidate_id', async ({ page }) => {
    let resumeBody = null
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: CANDIDATE_SSE })
    })
    await page.route('**/api/v1/inbox/ibx_cand_1/respond', async (route) => {
      resumeBody = route.request().postDataJSON()
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' },
        body: 'data: {"stage":"Execute SQL"}\n\ndata: {"sql":"SELECT 1","result":[{"ok":1}],"total_rows":1}\n\n' })
    })
    await page.route('**/api/v1/inbox/pending**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 0, items: [] }) })
    })

    await page.goto('/')
    await page.locator('textarea').fill('看下销售')
    await page.keyboard.press('Enter')

    const list = page.locator('.candidate-list')
    await expect(list).toBeVisible()

    // Click the second candidate (订单数).
    await list.locator('.candidate-card').nth(1).click()
    await list.locator('.candidate-actions button', { hasText: '执行此方案' }).click()

    await expect.poll(() => resumeBody !== null).toBe(true)
    expect(resumeBody.response).toMatchObject({
      response_type: 'candidate',
      candidate_id: 'cand_2',
      confirmed: true,
    })
  })

  test('clicking candidate toggles SQL preview', async ({ page }) => {
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: CANDIDATE_SSE })
    })
    await page.goto('/')
    await page.locator('textarea').fill('看下销售')
    await page.keyboard.press('Enter')

    const list = page.locator('.candidate-list')
    await expect(list).toBeVisible()

    // No preview initially.
    await expect(list.locator('.sql-preview')).toHaveCount(0)
    // Click first candidate → preview appears.
    await list.locator('.candidate-card').first().click()
    await expect(list.locator('.sql-preview')).toHaveCount(1)
    await expect(list.locator('.sql-preview pre').first()).toContainText('SELECT')
  })
})
