import { apiGet, apiPost } from '../utils/api.js'

export function getFilterValues(table, column, { limit = 100, offset = 0, search = '' } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (search) params.set('search', search)
  return apiGet(`/api/query/filters/${encodeURIComponent(table)}/${encodeURIComponent(column)}?${params}`)
}

export function executeFilteredSQL(sql, filters = []) {
  return apiPost('/api/query/execute', { sql, filters })
}
