import { ref } from 'vue'
import { executeFilteredSQL } from '../api/dashboardApi.js'

export function useDashboardFilters(backendMode = false) {
  const loading = ref(false)
  const error = ref('')
  const backendRows = ref([])

  function applyFilters(rows, filters) {
    if (!Array.isArray(rows) || !filters?.length) return rows
    return rows.filter(row => {
      return filters.every(f => matchFilter(row[f.field], f.operator, f.value))
    })
  }

  function matchFilter(value, operator, target) {
    if (value == null) return false
    const str = String(value).toLowerCase()
    const t = String(target).toLowerCase()
    const num = Number(value)
    const tt = Number(target)
    switch (operator) {
      case 'eq': return str === t
      case 'neq': return str !== t
      case 'contains': return str.includes(t)
      case 'gt': return !isNaN(num) && !isNaN(tt) && num > tt
      case 'lt': return !isNaN(num) && !isNaN(tt) && num < tt
      default: return true
    }
  }

  function toSQLWhere(filters) {
    return filters.map(f => {
      const val = typeof f.value === 'string' ? `'${f.value.replace(/'/g, "''")}'` : f.value
      switch (f.operator) {
        case 'eq': return `${f.field} = ${val}`
        case 'neq': return `${f.field} <> ${val}`
        case 'contains': return `${f.field} LIKE '%${f.value.replace(/'/g, "''")}%'`
        case 'gt': return `${f.field} > ${val}`
        case 'lt': return `${f.field} < ${val}`
        default: return ''
      }
    }).filter(Boolean).join(' AND ')
  }

  async function executeOnBackend(sql, filters) {
    if (!backendMode) return null
    loading.value = true
    error.value = ''
    try {
      const resp = await executeFilteredSQL(sql, filters)
      backendRows.value = resp?.rows || []
      return backendRows.value
    } catch (e) {
      error.value = String(e?.message || e)
      return null
    } finally {
      loading.value = false
    }
  }

  return { applyFilters, matchFilter, toSQLWhere, executeOnBackend, loading, error, backendRows }
}
