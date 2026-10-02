import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useClarifyStore } from '../../src/stores/clarify.js'

const CARD = {
  clarify_id: 'clr_abc123',
  question: '看下销售',
  missing_fields: [{ field: 'measure', reason: 'missing' }],
  suggestions: [
    { field: 'measure', prompt: '您想看哪个指标?', multi_select: false,
      allow_custom: true,
      options: [
        { label: 'GMV (总成交金额)', value: 'GMV', recommended: true },
        { label: '订单数', value: 'order_count' },
      ] },
  ],
  current_summary: '未识别到明确的指标,请补充',
}

describe('clarify store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    global.fetch = vi.fn()
  })

  it('setActiveCard populates card and clears selections', () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    expect(store.activeCard.clarify_id).toBe('clr_abc123')
    expect(store.selections).toEqual({})
  })

  it('clearActiveCard resets all state', () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    store.select('measure', 'GMV')
    store.clearActiveCard()
    expect(store.activeCard).toBeNull()
    expect(store.selections).toEqual({})
    expect(store.error).toBe('')
  })

  it('select replaces per-field value preserving others', () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    store.select('time', '上月')
    store.select('measure', 'GMV')
    expect(store.selections.measure).toBe('GMV')
    expect(store.selections.time).toBe('上月')
  })

  it('setGroupBy stores array selection', () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    store.setGroupBy(['C050', 'C021'])
    expect(store.selections.group_by).toEqual(['C050', 'C021'])
  })

  it('submitResponse confirmed clears active card', async () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'confirmed' }),
    })
    const result = await store.submitResponse({ confirmed: true })
    expect(result.status).toBe('confirmed')
    expect(store.activeCard).toBeNull()
  })

  it('submitResponse amended keeps card and clears selections for round 2', async () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    global.fetch.mockResolvedValue({
      ok: true,
      headers: { get: () => 'application/json' },
      json: async () => ({
        status: 'amended',
        merged_plan: { question: '看下销售' },
        missing_fields: [{ field: 'time', reason: 'missing' }],
        suggestions: [{ field: 'time', prompt: '时间?', options: [] }],
      }),
    })
    const result = await store.submitResponse({ confirmed: false })
    expect(result.status).toBe('amended')
    expect(store.activeCard).not.toBeNull()
    expect(store.activeCard.suggestions).toHaveLength(1)
    expect(store.selections).toEqual({})
  })

  it('submitResponse error sets store.error', async () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    global.fetch.mockResolvedValue({
      ok: false,
      status: 422,
      headers: { get: () => 'application/json' },
      json: async () => ({ message: '未能理解您的回复' }),
    })
    // Non-confirmed rounds still POST (amended dialog continues).
    await expect(store.submitResponse({ confirmed: false })).rejects.toThrow('未能理解您的回复')
    expect(store.error).toBe('未能理解您的回复')
  })

  it('cancel marks cancelled and clears', async () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    global.fetch.mockResolvedValue({
      ok: true,
      headers: { get: () => 'application/json' },
      json: async () => ({ status: 'cancelled' }),
    })
    const result = await store.cancel()
    expect(result.status).toBe('cancelled')
    expect(store.activeCard).toBeNull()
  })

  it('submitFreeText stages payload for the streamed resume', async () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    let called = false
    global.fetch.mockImplementation(() => { called = true; return Promise.resolve({}) })
    await store.submitFreeText('其实想看 7 月份的华北 GMV')
    // New M3 contract: confirm flows stage the payload; the view drives
    // ONE streamed POST to the inbox respond endpoint.
    expect(called).toBe(false)
    const staged = store.takePendingResponse()
    expect(staged.response_type).toBe('free_text')
    expect(staged.free_text).toContain('华北')
    expect(staged.confirmed).toBe(true)
  })

  it('submitCandidate stages candidate pick and clears card', async () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    let called = false
    global.fetch.mockImplementation(() => { called = true; return Promise.resolve({}) })
    const result = await store.submitCandidate('cand_xyz')
    expect(result.status).toBe('candidate_confirmed')
    expect(called).toBe(false)
    const staged = store.takePendingResponse()
    expect(staged).toMatchObject({
      response_type: 'candidate',
      candidate_id: 'cand_xyz',
      confirmed: true,
    })
    expect(store.activeCard).toBeNull()
  })

  it('amended round 2 payload is staged on next confirm', async () => {
    const store = useClarifyStore()
    store.setActiveCard(CARD)
    store.select('measure', 'GMV')
    global.fetch.mockResolvedValue({
      ok: true,
      headers: { get: () => 'application/json' },
      json: async () => ({
        status: 'amended',
        merged_plan: { question: '看下销售' },
        missing_fields: [{ field: 'time', reason: 'missing' }],
        suggestions: [{ field: 'time', prompt: '时间?', options: [] }],
      }),
    })
    await store.submitResponse({ confirmed: false })
    expect(store.activeCard.suggestions).toHaveLength(1)
    store.select('time', '上月')
    await store.submitResponse({ confirmed: true })
    const staged = store.takePendingResponse()
    expect(staged.selections).toEqual({ time: '上月' })
  })
})
