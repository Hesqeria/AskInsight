import { test, expect } from '@playwright/test'

/**
 * M3 inbox interaction E2E: approval flow + clarify resume + sidebar
 * pending section. All backend endpoints are route-mocked (SSE bodies).
 */

const b64url = (obj) => Buffer.from(JSON.stringify(obj))
  .toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const tokenFor = (sub, role) =>
  `${b64url({ alg: 'none', typ: 'JWT' })}.${b64url({ sub, role })}.sig`

const ADMIN_TOKEN = tokenFor('approver', 'L4_admin')
const USER_TOKEN = tokenFor('analyst', 'user')

const sse = (frames) => frames.join('')

const APPROVAL_SSE = sse([
  'data: {"request_id":"sess_appr_1"}\n\n',
  'data: {"stage":"Intent Recognition"}\n\n',
  'data: {"stage":"Correct SQL"}\n\n',
  'data: {"pending_approval":{"ticket_id":"tkt_1","request_id":"sess_appr_1",'
    + '"reason":"SQL references 2 PII L>= 3 column(s)",'
    + '"violations":[{"column_ref":"dw.customer.phone","class_name":"Phone","pii_level":4}],'
    + '"status":"pending","inbox_id":"ibx_appr_1",'
    + '"respond_url":"/api/v1/inbox/ibx_appr_1/respond"}}\n\n',
])

const APPROVED_RESUME_SSE = sse([
  'data: {"stage":"Execute SQL"}\n\n',
  'data: {"sql":"SELECT region FROM dw.sales",'
    + '"result":[{"region":"huabei"},{"region":"huanan"}],'
    + '"total_rows":2,"pii_masked_cells":0}\n\n',
  'data: {"resume_complete":true}\n\n',
])

test.describe('Approval flow (M3 inbox)', () => {
  test('L4 approver approves, execution resumes via inbox', async ({ page }) => {
    const calls = []
    await page.addInitScript((t) => localStorage.setItem('token', t), ADMIN_TOKEN)
    await page.route('**/api/query', async (route) => {
      calls.push('query')
      await route.fulfill({ status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: APPROVAL_SSE })
    })
    await page.route('**/api/v1/inbox/ibx_appr_1/respond', async (route) => {
      calls.push('respond')
      const body = route.request().postDataJSON()
      expect(body.response.decision).toBe('approved')
      await route.fulfill({ status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: APPROVED_RESUME_SSE })
    })
    await page.route('**/api/v1/inbox/pending**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 0, items: [] }) })
    })
    await page.goto('/')
    await page.locator('textarea').fill('query with pii')
    await page.keyboard.press('Enter')
    await expect(page.locator('.approval-card')).toBeVisible()
    await expect(page.locator('.ac-ticket')).toHaveText(/tkt_1/)
    await page.getByRole('button', { name: /批准|Approve/ }).click()
    await expect(page.locator('table, .el-table').first()).toBeVisible()
    expect(calls.filter((c) => c === 'query')).toHaveLength(1)
    expect(calls).toContain('respond')
  })

  test('non-approver sees waiting state, no decide buttons', async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('token', t), USER_TOKEN)
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: APPROVAL_SSE })
    })
    await page.route('**/api/v1/inbox/pending**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 0, items: [] }) })
    })
    await page.goto('/')
    await page.locator('textarea').fill('query with pii')
    await page.keyboard.press('Enter')
    await expect(page.locator('.approval-card')).toBeVisible()
    await expect(page.locator('.ac-status')).toBeVisible()
    await expect(page.locator('.ac-actions')).toHaveCount(0)
  })
})

const CLARIFY_SSE = sse([
  'data: {"request_id":"sess_clar_1"}\n\n',
  'data: {"stage":"Pending Clarification"}\n\n',
  'data: {"clarify_required":{"clarify_id":"clr_e2e_1","question":"GMV",'
    + '"missing_fields":[{"field":"time","reason":"missing"}],'
    + '"suggestions":[{"field":"time","prompt":"time?",'
    + '"multi_select":false,"allow_custom":true,'
    + '"options":[{"label":"benyue","value":"benyue"}]}],'
    + '"current_summary":"summary",'
    + '"inbox_id":"ibx_clar_1",'
    + '"respond_url":"/api/v1/inbox/ibx_clar_1/respond","status":"pending"}}\n\n',
])

const CLARIFY_RESUME_SSE = sse([
  'data: {"stage":"Execute SQL"}\n\n',
  'data: {"sql":"SELECT 1",'
    + '"result":[{"region":"huabei","gmv":999}],'
    + '"total_rows":1,"pii_masked_cells":0}\n\n',
])

test.describe('Clarify resume via inbox (M3)', () => {
  test('confirm posts to respond endpoint and streams the resume', async ({ page }) => {
    const calls = []
    let respondBody = null
    await page.addInitScript((t) => localStorage.setItem('token', t), USER_TOKEN)
    await page.route('**/api/query', async (route) => {
      calls.push('query')
      await route.fulfill({ status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: CLARIFY_SSE })
    })
    await page.route('**/api/v1/inbox/ibx_clar_1/respond', async (route) => {
      calls.push('respond')
      respondBody = route.request().postDataJSON()
      await route.fulfill({ status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: CLARIFY_RESUME_SSE })
    })
    await page.route('**/api/v1/inbox/pending**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 0, items: [] }) })
    })
    await page.goto('/')
    await page.locator('textarea').fill('GMV')
    await page.keyboard.press('Enter')
    await page.locator('.option-btn', { hasText: 'benyue' }).first().click()
    await page.getByRole('button', { name: /确认并执行/ }).click()
    // Single-row numeric result renders as a TABLE (detectType).
    await expect(page.locator('table, .el-table').first()).toBeVisible()
    expect(calls.filter((c) => c === 'query')).toHaveLength(1)
    expect(calls).toContain('respond')
    expect(respondBody.response.response_type).toBe('selection')
  })
})

test.describe('Sidebar pending interactions', () => {
  test('amber pending section lists inbox items', async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('token', t), USER_TOKEN)
    await page.route('**/api/v1/inbox/pending**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({
          count: 2,
          items: [
            { inbox_id: 'ibx_1', kind: 'approval', status: 'pending',
              channel: 'next_step', payload: {}, created_at: '2026-08-19 10:00:00' },
            { inbox_id: 'ibx_2', kind: 'clarify', status: 'pending',
              channel: 'next_step', payload: {}, created_at: '2026-08-19 11:00:00' },
          ],
        }) })
    })
    await page.goto('/')
    const section = page.locator('.sidebar-section').filter({ hasText: /待处理|Pending/ })
    await expect(section).toBeVisible()
    await expect(section.locator('.pending-badge')).toHaveText('2')
    await expect(section.locator('.pending-item').filter({ hasText: '审批' })).toBeVisible()
    await expect(section.locator('.amber-dot').first()).toBeVisible()
  })
})

test.describe('Spill view full result (M10)', () => {
  test('spilled result shows view-full button and paginates', async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('token', t), USER_TOKEN)
    const spillSSE = sse([
      'data: {"stage":"Execute SQL"}\n\n',
      'data: {"sql":"SELECT 1","result":[{"id":1},{"__omitted__":100}],'
        + '"total_rows":152,"pii_masked_cells":0,"spill_id":"spill_e2e_1"}\n\n',
    ])
    await page.route('**/api/query', async (route) => {
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: spillSSE })
    })
    let spillRequested = false
    await page.route('**/api/v1/spills/spill_e2e_1**', async (route) => {
      spillRequested = true
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ found: true, spill_id: 'spill_e2e_1',
          row_count: 152, offset: 0, rows: [{ id: 1 }, { id: 2 }] }) })
    })
    await page.route('**/api/v1/inbox/pending**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 0, items: [] }) })
    })
    await page.goto('/')
    await page.locator('textarea').fill('big result')
    await page.keyboard.press('Enter')
    const btn = page.getByRole('button', { name: '查看全量' })
    await expect(btn).toBeVisible()
    await btn.click()
    await expect(page.locator('.el-dialog')).toBeVisible()
    expect(spillRequested).toBe(true)
  })
})
