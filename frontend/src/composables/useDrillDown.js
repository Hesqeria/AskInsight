import { reactive } from 'vue'

export function useDrillDown() {
  const state = reactive({
    paths: {},
  })

  function push(widgetId, field, value) {
    if (!state.paths[widgetId]) state.paths[widgetId] = []
    state.paths[widgetId].push({ field, value })
  }

  function slice(widgetId, index) {
    if (!state.paths[widgetId]) return
    state.paths[widgetId] = state.paths[widgetId].slice(0, index + 1)
  }

  function clear(widgetId) {
    state.paths[widgetId] = []
  }

  function getPath(widgetId) {
    return state.paths[widgetId] || []
  }

  function buildSQL(originalSQL, widgetId) {
    const path = getPath(widgetId)
    if (!path.length) return originalSQL
    const conditions = path.map(p => `${p.field} = '${String(p.value).replace(/'/g, "''")}'`).join(' AND ')
    const upper = originalSQL.trim().toUpperCase()
    if (upper.includes(' WHERE ')) {
      const idx = originalSQL.toUpperCase().indexOf(' WHERE ') + 7
      return originalSQL.slice(0, idx) + `(${conditions}) AND ` + originalSQL.slice(idx)
    }
    const orderIdx = upper.indexOf(' ORDER BY ')
    if (orderIdx > -1) {
      return originalSQL.slice(0, orderIdx) + ` WHERE ${conditions}` + originalSQL.slice(orderIdx)
    }
    return `${originalSQL} WHERE ${conditions}`
  }

  return { state, push, slice, clear, getPath, buildSQL }
}
