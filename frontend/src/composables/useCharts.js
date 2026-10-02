import { ref, onUnmounted } from 'vue'
import * as echarts from 'echarts/core'
import {
  BarChart, LineChart, PieChart, ScatterChart,
  RadarChart, FunnelChart, GaugeChart, TreemapChart, HeatmapChart,
} from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  MarkLineComponent, MarkPointComponent, ToolboxComponent, DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useEchartsTheme, lightTheme, darkTheme } from './useEchartsTheme.js'

echarts.use([
  BarChart, LineChart, PieChart, ScatterChart, RadarChart,
  FunnelChart, GaugeChart, TreemapChart, HeatmapChart,
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  MarkLineComponent, MarkPointComponent, ToolboxComponent, DataZoomComponent,
  CanvasRenderer,
])

export const CHART_TYPES = {
  bar: '柱状图',
  line: '折线图',
  pie: '饼图',
  area: '面积图',
  scatter: '散点图',
  stacked_bar: '堆叠柱状图',
  stacked_area: '堆叠面积图',
  big_number: 'KPI 指标卡',
  dual_axis: '双轴混合图',
  radar: '雷达图',
  heatmap: '热力图',
  funnel: '漏斗图',
  gauge: '仪表盘',
  treemap: '矩形树图',
  table: '明细表格',
}

export function useCharts() {
  const instances = ref([])
  const { isDark, sync: syncTheme } = useEchartsTheme()

  function getCommonOption() {
    return {
      tooltip: { trigger: 'axis' },
      legend: { top: 0, type: 'scroll', textStyle: { fontSize: 12 } },
      // one-click PNG export (pandas-ai #174/#212/#323 chart-save pain)
      toolbox: {
        feature: { saveAsImage: { title: '保存为图片', name: 'AskInsight图表' } },
        right: 8,
      },
      grid: { left: 50, right: 20, top: 40, bottom: 50 },
    }
  }

  function buildBarOption(rows, categoryKey, valueKeys, stacked = false) {
    const categoryData = rows.map(r => String(r[categoryKey] || ''))
    return {
      ...getCommonOption(),
      xAxis: { type: 'category', data: categoryData, axisLabel: { rotate: 30, fontSize: 11 } },
      yAxis: { type: 'value' },
      series: valueKeys.map(k => ({
        type: 'bar',
        name: k,
        data: rows.map(r => Number(r[k]) || 0),
        stack: stacked ? 'total' : undefined,
        itemStyle: { borderRadius: stacked ? 0 : [4, 4, 0, 0] },
      })),
    }
  }

  function buildLineOption(rows, categoryKey, valueKeys, area = false, stacked = false) {
    const categoryData = rows.map(r => String(r[categoryKey] || ''))
    return {
      ...getCommonOption(),
      xAxis: { type: 'category', data: categoryData, axisLabel: { rotate: 30, fontSize: 11 } },
      yAxis: { type: 'value' },
      series: valueKeys.map(k => ({
        type: 'line',
        name: k,
        data: rows.map(r => Number(r[k]) || 0),
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: area ? { opacity: 0.3 } : undefined,
        stack: stacked ? 'total' : undefined,
      })),
    }
  }

  function buildPieOption(rows, categoryKey, valueKeys) {
    const valueKey = valueKeys[0] || categoryKey
    return {
      tooltip: { trigger: 'item' },
      legend: { top: 0, type: 'scroll' },
      toolbox: {
        feature: { saveAsImage: { title: '保存为图片', name: 'AskInsight图表' } },
        right: 8,
      },
      series: [{
        type: 'pie',
        name: valueKey,
        radius: ['40%', '70%'],
        data: rows.map(r => ({
          name: String(r[categoryKey] || ''),
          value: Number(r[valueKey]) || 0,
        })),
        label: { show: true, formatter: '{b}: {d}%' },
      }],
    }
  }

  function buildScatterOption(rows, categoryKey, valueKeys) {
    const x = valueKeys[0] || categoryKey
    const y = valueKeys[1] || valueKeys[0] || categoryKey
    return {
      ...getCommonOption(),
      xAxis: { type: 'value', name: x },
      yAxis: { type: 'value', name: y },
      series: [{
        type: 'scatter',
        data: rows.map(r => [Number(r[x]) || 0, Number(r[y]) || 0]),
        symbolSize: 10,
      }],
    }
  }

  function buildBigNumberOption(rows, valueKeys) {
    const valueKey = valueKeys[0]
    const first = rows[0] || {}
    const value = Number(first[valueKey]) || 0
    return {
      title: {
        text: value.toLocaleString(),
        left: 'center', top: 'center',
        textStyle: { fontSize: 48, fontWeight: 'bold' },
      },
      series: [],
    }
  }

  function buildDualAxisOption(rows, categoryKey, valueKeys) {
    const categoryData = rows.map(r => String(r[categoryKey] || ''))
    const barKey = valueKeys[0] || categoryKey
    const lineKey = valueKeys[1] || valueKeys[0] || categoryKey
    return {
      ...getCommonOption(),
      xAxis: { type: 'category', data: categoryData, axisLabel: { rotate: 30, fontSize: 11 } },
      yAxis: [
        { type: 'value', name: barKey, position: 'left' },
        { type: 'value', name: lineKey, position: 'right' },
      ],
      series: [
        { type: 'bar', name: barKey, data: rows.map(r => Number(r[barKey]) || 0), itemStyle: { borderRadius: [4, 4, 0, 0] } },
        { type: 'line', name: lineKey, yAxisIndex: 1, data: rows.map(r => Number(r[lineKey]) || 0), smooth: true, symbol: 'circle' },
      ],
    }
  }

  function buildRadarOption(rows, categoryKey, valueKeys) {
    const indicators = valueKeys.map(k => ({
      name: k,
      max: Math.max(...rows.map(r => Number(r[k]) || 0), 1),
    }))
    return {
      tooltip: { trigger: 'item' },
      radar: { indicator: indicators, radius: '65%' },
      series: [{
        type: 'radar',
        data: rows.slice(0, 5).map(r => ({
          name: String(r[categoryKey] || ''),
          value: valueKeys.map(k => Number(r[k]) || 0),
        })),
      }],
    }
  }

  function buildHeatmapOption(rows, categoryKey, valueKeys) {
    const xField = categoryKey
    const yField = valueKeys[0] || xField
    const valueField = valueKeys[1] || valueKeys[0] || xField
    const xSet = [...new Set(rows.map(r => String(r[xField] || '')))]
    const ySet = [...new Set(rows.map(r => String(r[yField] || '')))]
    const data = rows.map(r => [
      xSet.indexOf(String(r[xField] || '')),
      ySet.indexOf(String(r[yField] || '')),
      Number(r[valueField]) || 0,
    ])
    return {
      tooltip: { position: 'top' },
      grid: { left: 80, right: 20, top: 20, bottom: 60 },
      xAxis: { type: 'category', data: xSet, splitArea: { show: true } },
      yAxis: { type: 'category', data: ySet, splitArea: { show: true } },
      visualMap: {
        min: 0,
        max: Math.max(...data.map(d => d[2]), 1),
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: '0%',
      },
      series: [{ type: 'heatmap', data, label: { show: true } }],
    }
  }

  function buildFunnelOption(rows, categoryKey, valueKeys) {
    const valueKey = valueKeys[0] || categoryKey
    const sorted = [...rows].sort((a, b) => (Number(b[valueKey]) || 0) - (Number(a[valueKey]) || 0))
    return {
      tooltip: { trigger: 'item' },
      series: [{
        type: 'funnel',
        sort: 'descending',
        gap: 2,
        label: { show: true, position: 'inside' },
        data: sorted.map(r => ({
          name: String(r[categoryKey] || ''),
          value: Number(r[valueKey]) || 0,
        })),
      }],
    }
  }

  function buildGaugeOption(rows, valueKeys) {
    const valueKey = valueKeys[0]
    const value = Number((rows[0] || {})[valueKey]) || 0
    return {
      series: [{
        type: 'gauge',
        startAngle: 180, endAngle: 0,
        min: 0, max: Math.max(100, value * 1.2),
        splitNumber: 10,
        axisLine: { lineStyle: { width: 6 } },
        pointer: { length: '60%', width: 6 },
        axisTick: { length: 12, lineStyle: { width: 2 } },
        splitLine: { length: 20, lineStyle: { width: 3 } },
        axisLabel: { distance: 25, fontSize: 14 },
        detail: { fontSize: 30, offsetCenter: [0, '0%'], valueAnimation: true, formatter: '{value}%' },
        data: [{ value, name: valueKey }],
      }],
    }
  }

  function buildTreemapOption(rows, categoryKey, valueKeys) {
    const valueKey = valueKeys[0] || categoryKey
    return {
      tooltip: { trigger: 'item' },
      series: [{
        type: 'treemap',
        data: rows.map(r => ({
          name: String(r[categoryKey] || ''),
          value: Number(r[valueKey]) || 0,
        })),
      }],
    }
  }

  function buildTableOption(rows, categoryKey, valueKeys) {
    return {
      _isTable: true,
      columns: [categoryKey, ...valueKeys],
      rows,
    }
  }

  function getChartOption(type, rows, categoryKey, valueKeys) {
    const safeRows = Array.isArray(rows) ? rows : []
    if (!safeRows.length) return {}

    switch (type) {
      case 'bar': return buildBarOption(safeRows, categoryKey, valueKeys)
      case 'line': return buildLineOption(safeRows, categoryKey, valueKeys)
      case 'area': return buildLineOption(safeRows, categoryKey, valueKeys, true)
      case 'pie': return buildPieOption(safeRows, categoryKey, valueKeys)
      case 'scatter': return buildScatterOption(safeRows, categoryKey, valueKeys)
      case 'stacked_bar': return buildBarOption(safeRows, categoryKey, valueKeys, true)
      case 'stacked_area': return buildLineOption(safeRows, categoryKey, valueKeys, true, true)
      case 'big_number': return buildBigNumberOption(safeRows, valueKeys)
      case 'dual_axis': return buildDualAxisOption(safeRows, categoryKey, valueKeys)
      case 'radar': return buildRadarOption(safeRows, categoryKey, valueKeys)
      case 'heatmap': return buildHeatmapOption(safeRows, categoryKey, valueKeys)
      case 'funnel': return buildFunnelOption(safeRows, categoryKey, valueKeys)
      case 'gauge': return buildGaugeOption(safeRows, valueKeys)
      case 'treemap': return buildTreemapOption(safeRows, categoryKey, valueKeys)
      case 'table': return buildTableOption(safeRows, categoryKey, valueKeys)
      default: return buildBarOption(safeRows, categoryKey, valueKeys)
    }
  }

  function isTable(type) {
    return type === 'table' || type === 'big_number'
  }

  function recommendChartType(rows) {
    if (!Array.isArray(rows) || !rows.length) return 'table'
    const keys = Object.keys(rows[0])
    if (keys.length === 1 && rows.length === 1) return 'big_number'
    if (keys.length === 1) return 'table'
    if (keys.length === 2) {
      const numericKeys = keys.filter(k => typeof rows[0][k] === 'number')
      const textKeys = keys.filter(k => typeof rows[0][k] !== 'number')
      if (numericKeys.length === 1 && textKeys.length === 1) {
        if (rows.length <= 6) return 'pie'
        const firstText = String(rows[0][textKeys[0]])
        if (/^\d{4}[-/]\d{2}/.test(firstText)) return 'line'
        return 'bar'
      }
    }
    if (keys.length === 3) {
      const numericKeys = keys.filter(k => typeof rows[0][k] === 'number')
      if (numericKeys.length === 1) return 'heatmap'
      if (numericKeys.length >= 2) return 'stacked_area'
    }
    return 'bar'
  }

  function initChart(el, type, rows, categoryKey, valueKeys) {
    if (!el) return null
    disposeChart(el)
    syncTheme()
    const chart = echarts.init(el, isDark.value ? darkTheme : lightTheme, { renderer: 'canvas' })
    const option = getChartOption(type, rows, categoryKey, valueKeys)
    if (!option._isTable) {
      chart.setOption(option, true)
    }
    instances.value.push(chart)
    return chart
  }

  function disposeChart(el) {
    if (!el) return
    const chart = echarts.getInstanceByDom(el)
    if (chart) {
      chart.dispose()
      instances.value = instances.value.filter(c => c !== chart)
    }
  }

  function resizeAll() {
    instances.value.forEach(chart => {
      try { chart.resize() } catch {}
    })
  }

  onUnmounted(() => {
    instances.value.forEach(c => { try { c.dispose() } catch {} })
    instances.value = []
  })

  return {
    CHART_TYPES,
    getChartOption,
    initChart,
    disposeChart,
    resizeAll,
    recommendChartType,
    isTable,
  }
}
