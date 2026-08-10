import { reactive, watch } from 'vue'

export function useChartConfig(initial = {}) {
  const config = reactive({
    type: initial.type || 'bar',
    categoryKey: initial.categoryKey || '',
    valueKeys: initial.valueKeys ? [...initial.valueKeys] : [],
    title: initial.title || '',
    filters: initial.filters ? [...initial.filters] : [],
    drillDown: initial.drillDown || false,
  })

  function update(patch) {
    Object.assign(config, patch)
  }

  function reset() {
    config.type = 'bar'
    config.categoryKey = ''
    config.valueKeys = []
    config.title = ''
    config.filters = []
    config.drillDown = false
  }

  function applyRows(rows) {
    if (!Array.isArray(rows) || !rows.length) return
    const keys = Object.keys(rows[0])
    if (!config.categoryKey && keys.length) config.categoryKey = keys[0]
    if (!config.valueKeys.length) {
      config.valueKeys = keys.slice(1).filter(k => {
        const v = rows[0][k]
        return typeof v === 'number' || !isNaN(Number(v))
      })
    }
  }

  return { config, update, reset, applyRows }
}
