<template>
  <div class="chart-wrap" ref="wrapEl">
    <chart-actions
      :sql="sql"
      @download-png="onDownloadPng"
      @download-csv="onDownloadCsv"
      @copy-sql="onCopySql"
    />
    <div v-if="isTable" class="table-view">
      <div v-if="type === 'big_number'" class="big-number">
        <div class="big-number-value">{{ bigNumberValue }}</div>
        <div class="big-number-label">{{ valueKeys[0] || '' }}</div>
      </div>
      <div v-else class="plain-table-wrap">
        <table class="plain-table">
          <thead>
            <tr><th v-for="col in tableColumns" :key="col">{{ col }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in tableRows" :key="i">
              <td v-for="col in tableColumns" :key="col">{{ row[col] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div v-else ref="chartEl" class="chart-box"></div>
    <div class="chart-type-badge">{{ chartTypeLabel }}</div>
  </div>
</template>

<script setup>
import { ref, shallowRef, onMounted, onUnmounted, watch, nextTick, computed } from 'vue'
import { useCharts } from '../../composables/useCharts.js'
import { useChartExport } from '../../composables/useChartExport.js'
import { useEchartsTheme } from '../../composables/useEchartsTheme.js'
import ChartActions from './ChartActions.vue'

const props = defineProps({
  type: { type: String, default: 'bar' },
  categoryKey: { type: String, default: '' },
  valueKeys: { type: Array, default: () => [] },
  rows: { type: Array, default: () => [] },
  sql: { type: String, default: '' },
})
const emit = defineEmits(['click-data', 'drill-down'])

const { initChart, disposeChart, CHART_TYPES } = useCharts()
const { isDark } = useEchartsTheme()
const { downloadPNG, downloadCSV, copySQL } = useChartExport()
const chartEl = ref(null)
const wrapEl = ref(null)
const chartInstance = shallowRef(null)
let resizeObserver = null

const chartTypeLabel = computed(() => CHART_TYPES[props.type] || props.type)
const isTable = computed(() => props.type === 'table' || props.type === 'big_number')
const tableColumns = computed(() => {
  if (!props.rows.length) return []
  return [props.categoryKey, ...props.valueKeys].filter(Boolean)
})
const tableRows = computed(() => props.rows || [])
const bigNumberValue = computed(() => {
  const first = props.rows[0] || {}
  const value = Number(first[props.valueKeys[0]]) || 0
  return value.toLocaleString()
})

function onClick(params) {
  emit('click-data', params)
  if (params?.componentType === 'series' && params?.name) {
    emit('drill-down', { field: props.categoryKey, value: params.name })
  }
}

function render() {
  if (isTable.value) {
    if (chartInstance.value) {
      disposeChart(chartEl.value)
      chartInstance.value = null
    }
    return
  }
  if (!chartEl.value || !props.rows.length) return
  chartInstance.value = initChart(chartEl.value, props.type, props.rows, props.categoryKey, props.valueKeys)
  chartInstance.value?.off('click')
  chartInstance.value?.on('click', onClick)
}

function onDownloadPng() {
  downloadPNG(chartInstance.value, `chart-${props.type}.png`)
}

function onDownloadCsv() {
  downloadCSV(props.rows, tableColumns.value, `data-${props.type}.csv`)
}

function onCopySql() {
  copySQL(props.sql)
}

onMounted(() => {
  nextTick(render)
  resizeObserver = new ResizeObserver(() => chartInstance.value?.resize())
  if (wrapEl.value) resizeObserver.observe(wrapEl.value)
})

watch(() => [props.type, props.rows, props.categoryKey, props.valueKeys], render, { deep: true })
watch(isDark, render)

onUnmounted(() => {
  resizeObserver?.disconnect()
  disposeChart(chartEl.value)
  chartInstance.value = null
})

defineExpose({ chartInstance, getChartEl: () => chartEl.value })
</script>

<style scoped>
.chart-wrap { position: relative; width: 100%; height: 100%; }
.chart-wrap:hover :deep(.chart-actions) { opacity: 1; pointer-events: auto; }
.chart-box { width: 100%; height: 100%; min-height: 220px; }
.chart-type-badge { position: absolute; top: 4px; right: 8px; font-size: 10px; color: var(--color-text-muted); text-transform: uppercase; opacity: 0.5; pointer-events: none; }
.table-view { width: 100%; height: 100%; padding: 8px; box-sizing: border-box; overflow: auto; }
.plain-table-wrap { max-height: 100%; overflow: auto; }
.plain-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.plain-table th, .plain-table td { border: 1px solid var(--color-border); padding: 6px 8px; text-align: left; }
.plain-table th { background: var(--color-bg-tertiary); position: sticky; top: 0; }
.big-number { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; }
.big-number-value { font-size: 42px; font-weight: bold; color: var(--color-primary); }
.big-number-label { font-size: 14px; color: var(--color-text-muted); margin-top: 8px; }
</style>
