import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock localStorage for api.js token retrieval
const store = {}
const localStorageMock = {
  getItem: vi.fn((k) => store[k] ?? null),
  setItem: vi.fn((k, v) => { store[k] = String(v) }),
  removeItem: vi.fn((k) => { delete store[k] }),
  clear: vi.fn(() => { Object.keys(store).forEach((k) => delete store[k]) }),
}
vi.stubGlobal('localStorage', localStorageMock)

import { getFilterValues, executeFilteredSQL } from '../../src/api/dashboardApi.js'
import { useDashboardFilters } from '../../src/composables/useDashboardFilters.js'

describe('dashboardApi.getFilterValues', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => vi.restoreAllMocks())

  it('calls /api/query/filters with encoded path and query params', async () => {
    fetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ values: ['a', 'b'] }),
    })
    const res = await getFilterValues('dim region', 'col/x', { limit: 10, offset: 5, search: 'e' })
    expect(res.values).toEqual(['a', 'b'])
    expect(fetch).toHaveBeenCalledTimes(1)
    const url = fetch.mock.calls[0][0]
    expect(url).toContain('/api/query/filters/dim%20region/col%2Fx')
    expect(url).toContain('limit=10')
    expect(url).toContain('offset=5')
    expect(url).toContain('search=e')
  })

  it('omits search when empty', async () => {
    fetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ values: [] }) })
    await getFilterValues('t', 'c')
    const url = fetch.mock.calls[0][0]
    expect(url).not.toContain('search=')
  })
})

describe('dashboardApi.executeFilteredSQL', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
  afterEach(() => vi.restoreAllMocks())

  it('POSTs sql and filters to /api/query/execute', async () => {
    fetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ rows: [{ x: 1 }] }) })
    const res = await executeFilteredSQL('SELECT 1', [{ field: 'x', operator: 'eq', value: '1' }])
    expect(res.rows).toEqual([{ x: 1 }])
    const [url, opts] = fetch.mock.calls[0]
    expect(url).toBe('/api/query/execute')
    expect(opts.method).toBe('POST')
    const body = JSON.parse(opts.body)
    expect(body.sql).toBe('SELECT 1')
    expect(body.filters[0].field).toBe('x')
  })
})

describe('useDashboardFilters logic', () => {
  it('applyFilters filters client-side rows', () => {
    const { applyFilters } = useDashboardFilters(false)
    const rows = [
      { region: 'east', amount: 100 },
      { region: 'north', amount: 200 },
    ]
    const out = applyFilters(rows, [{ field: 'region', operator: 'eq', value: 'east' }])
    expect(out).toEqual([{ region: 'east', amount: 100 }])
  })

  it('toSQLWhere builds AND-joined conditions', () => {
    const { toSQLWhere } = useDashboardFilters(false)
    const sql = toSQLWhere([
      { field: 'region', operator: 'eq', value: 'east' },
      { field: 'amount', operator: 'gt', value: 50 },
    ])
    expect(sql).toContain("region = 'east'")
    expect(sql).toContain('amount > 50')
    expect(sql).toContain(' AND ')
  })

  it('executeOnBackend is a no-op in client mode', async () => {
    const { executeOnBackend } = useDashboardFilters(false)
    const res = await executeOnBackend('SELECT 1', [])
    expect(res).toBeNull()
  })

  it('executeOnBackend calls API in backend mode and stores rows', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ rows: [{ a: 1 }] }),
    }))
    const { executeOnBackend, backendRows } = useDashboardFilters(true)
    const res = await executeOnBackend('SELECT 1', [])
    expect(res).toEqual([{ a: 1 }])
    expect(backendRows.value).toEqual([{ a: 1 }])
    vi.unstubAllGlobals()
  })
})
