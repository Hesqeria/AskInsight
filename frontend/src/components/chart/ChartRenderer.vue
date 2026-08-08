<template>
  <div class="chart-wrap">
    <div ref="chartEl" class="chart-box"></div>
    <div class="chart-type-badge">{{ chartTypeLabel }}</div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useCharts } from '../../composables/useCharts.js'

const props = defineProps({
  type: { type: String, default: 'bar' },
  categoryKey: { type: String, default: '' },
  valueKeys: { type: Array, default: () => [] },
  rows: { type: Array, default: () => [] },
})

const { initChart, disposeChart, getChartConfig, CHART_TYPES } = useCharts()
const chartEl = ref(null)
let chartInstance = null

const chartTypeLabel = props.type.replace('_', ' ')

function render() {
  if (!chartEl.value || !props.rows.length) return
  disposeChart(chartEl.value)
  const categoryData = props.rows.map(r => String(r[props.categoryKey] || ''))
  chartInstance = initChart(chartEl.value, props.type, categoryData, props.valueKeys, props.rows)
}

onMounted(() => nextTick(render))
watch(() => [props.type, props.rows, props.categoryKey], render, { deep: true })

onUnmounted(() => {
  disposeChart(chartEl.value)
})
</script>

<style scoped>
.chart-wrap { position: relative; width: 100%; }
.chart-box { width: 100%; height: 320px; }
.chart-type-badge { position: absolute; top: 4px; right: 8px; font-size: 10px; color: var(--color-text-muted); text-transform: uppercase; opacity: 0.5; }
</style>
