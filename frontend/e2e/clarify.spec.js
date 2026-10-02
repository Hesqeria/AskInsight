import { test, expect } from '@playwright/test'

/**
 * Clarification workflow E2E tests.
 *
 * Strategy: intercept /api/query (SSE) and /api/v1/clarify/* with route
 * mocking so tests run without a live backend.
 *
 * Two clarify card variants:
 *   - Wizard (vague, measure missing): shows GuidedWizard steps
 *   - Card (specific-ish, only time missing): shows ClarifyCard options
 */

// Scenario: measure missing → GuidedWizard (very vague question).
const WIZARD_SSE = [
  'data: {"stage":"Intent Recognition"}\n\n',
  'data: {"stage":"Semantic Grounding"}\n\n',
  'data: {"stage":"Pending Clarification"}\n\n',
  'data: {"clarify_required":{"clarify_id":"clr_wizard_1","question":"最近表现如何",'
    + '"missing_fields":[{"field":"measure","reason":"missing"},{"field":"time","reason":"missing"}],'
    + '"suggestions":['
    + '{"field":"measure","prompt":"您想看哪个指标?","multi_select":false,"allow_custom":true,'
    + '"options":[{"label":"GMV (总成交金额)","value":"GMV","description":"已支付订单金额总和","recommended":true},'
    + '{"label":"订单数","value":"order_count","description":"订单总数量"},'
    + '{"label":"DAU (日活跃用户)","value":"DAU","description":"当日去重用户数"}]},'
    + '{"field":"time","prompt":"需要补充时间范围:","multi_select":false,"allow_custom":true,'
    + '"options":[{"label":"今天","value":"今天"},{"label":"昨天","value":"昨天"},'
    + '{"label":"近7天","value":"近7天"},{"label":"本月","value":"本月"},{"label":"上月","value":"上月"}]},'
    + '{"field":"group_by","prompt":"是否需要按维度分组?(可选)","multi_select":true,"allow_custom":false,'
    + '"options":[{"label":"不分组","value":null},{"label":"按地区","value":"C050"},'
    + '{"label":"按商品","value":"C020"}]}'
    + '],'
    + '"current_summary":"未识别到明确的指标,请补充",'
    + '"resume_url":"/api/v1/clarify/clr_wizard_1/resume",'
    + '"inbox_id":"ibx_wizard_1","respond_url":"/api/v1/inbox/ibx_wizard_1/respond","status":"pending"}}\n\n',
].join('')

// Scenario: measure known, time missing → ClarifyCard (single-select time).
const CARD_SSE = [
  'data: {"stage":"Intent Recognition"}\n\n',
  'data: {"stage":"Semantic Grounding"}\n\n',
  'data: {"stage":"Pending Clarification"}\n\n',
  'data: {"clarify_required":{"clarify_id":"clr_card_1","question":"华北的GMV",'
    + '"missing_fields":[{"field":"time","reason":"missing"}],'
    + '"suggestions":['
    + '{"field":"time","prompt":"需要补充时间范围:","multi_select":false,"allow_custom":true,'
    + '"options":[{"label":"今天","value":"今天"},{"label":"昨天","value":"昨天"},'
    + '{"label":"近7天","value":"近7天"},{"label":"本月","value":"本月"},{"label":"上月","value":"上月"}]}'
    + '],'
    + '"current_summary":"查询订单的总成交金额,筛选地域=华北,时间未指定",'
    + '"resume_url":"/api/v1/clarify/clr_card_1/resume",'
    + '"inbox_id":"ibx_card_1","respond_url":"/api/v1/inbox/ibx_card_1/respond","status":"pending"}}\n\n',
].join('')

test.describe('Clarification card flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('token', 'fake-test-token')
    })
  })

  test('shows wizard for very vague question (measure missing)', async ({ page }) => {
    await page.route('**/api/query', async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: WIZARD_SSE,
      })
    })
    await page.goto('/')
    await page.locator('textarea').fill('最近表现如何')
    await page.keyboard.press('Enter')

    // GuidedWizard appears with 3 measure options.
    const wizard = page.locator('.guided-wizard')
    await expect(wizard).toBeVisible()
    await expect(wizard.locator('.step-question')).toContainText('哪个指标')
    await expect(wizard.locator('.wizard-option')).toHaveCount(3)
  })

  test('wizard steps through measure → time → group', async ({ page }) => {
    let resumeBody = null
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: WIZARD_SSE })
    })
    await page.route('**/api/v1/inbox/ibx_wizard_1/respond', async (route) => {
      resumeBody = route.request().postDataJSON()
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' },
        body: 'data: {"stage":"Execute SQL"}\n\ndata: {"sql":"SELECT 1","result":[{"ok":1}],"total_rows":1}\n\n' })
    })
    await page.route('**/api/v1/inbox/pending**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 0, items: [] }) })
    })

    await page.goto('/')
    await page.locator('textarea').fill('最近表现如何')
    await page.keyboard.press('Enter')

    const wizard = page.locator('.guided-wizard')
    await expect(wizard).toBeVisible()

    // Step 0: pick GMV.
    await wizard.locator('.wizard-option', { hasText: 'GMV' }).click()
    await wizard.locator('button', { hasText: '下一步' }).click()

    // Step 1: pick 上月.
    await expect(wizard.locator('.step-question')).toContainText('时间范围')
    await wizard.locator('.wizard-option', { hasText: '上月' }).click()
    await wizard.locator('button', { hasText: '下一步' }).click()

    // Step 2: pick 按地区 then finish.
    await expect(wizard.locator('.step-question')).toContainText('分组')
    await wizard.locator('.wizard-option', { hasText: '按地区' }).click()
    await wizard.locator('button', { hasText: '完成并执行' }).click()

    await expect.poll(() => resumeBody !== null).toBe(true)
    expect(resumeBody.response).toMatchObject({
      response_type: 'selection',
      confirmed: true,
      selections: { measure: 'GMV', time: '上月', group_by: ['C050'] },
    })
  })

  test('shows ClarifyCard when only time missing', async ({ page }) => {
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: CARD_SSE })
    })
    await page.goto('/')
    await page.locator('textarea').fill('华北的GMV')
    await page.keyboard.press('Enter')

    const card = page.locator('.clarify-card')
    await expect(card).toBeVisible()
    await expect(card.locator('.option-btn')).toHaveCount(5)  // 5 time options
  })

  test('selecting time on card and confirming calls resume', async ({ page }) => {
    let resumeBody = null
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: CARD_SSE })
    })
    await page.route('**/api/v1/inbox/ibx_card_1/respond', async (route) => {
      resumeBody = route.request().postDataJSON()
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' },
        body: 'data: {"stage":"Execute SQL"}\n\ndata: {"sql":"SELECT 1","result":[{"ok":1}],"total_rows":1}\n\n' })
    })
    await page.route('**/api/v1/inbox/pending**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 0, items: [] }) })
    })

    await page.goto('/')
    await page.locator('textarea').fill('华北的GMV')
    await page.keyboard.press('Enter')

    const card = page.locator('.clarify-card')
    await expect(card).toBeVisible()
    await card.locator('.option-btn', { hasText: '上月' }).click()
    await card.locator('.clarify-actions button', { hasText: '确认并执行' }).click()

    await expect.poll(() => resumeBody !== null).toBe(true)
    expect(resumeBody.response).toMatchObject({
      response_type: 'selection',
      confirmed: true,
      selections: { time: '上月' },
    })
  })

  test('advanced editor drawer opens', async ({ page }) => {
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: CARD_SSE })
    })
    await page.goto('/')
    await page.locator('textarea').fill('华北的GMV')
    await page.keyboard.press('Enter')

    await expect(page.locator('.clarify-card')).toBeVisible()
    await page.locator('.open-editor-btn').click()
    await expect(page.locator('.el-drawer').first()).toBeVisible()
    await expect(page.locator('.el-drawer .el-select').first()).toBeVisible()
  })

  test('cancel removes the clarify card', async ({ page }) => {
    let cancelCalled = false
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: CARD_SSE })
    })
    await page.route('**/api/v1/clarify/clr_card_1/resume', async (route) => {
      cancelCalled = true
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'cancelled' }) })
    })

    await page.goto('/')
    await page.locator('textarea').fill('华北的GMV')
    await page.keyboard.press('Enter')

    const card = page.locator('.clarify-card')
    await expect(card).toBeVisible()
    await card.locator('.clarify-actions button', { hasText: '取消' }).click()

    await expect(page.locator('.clarify-card')).toHaveCount(0)
    expect(cancelCalled).toBe(true)
  })
})
