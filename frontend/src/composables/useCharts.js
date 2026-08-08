import { ref, onUnmounted } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, PieChart, ScatterChart,
  GridComponent, TooltipComponent, LegendComponent, TitleComponent, CanvasRenderer])

const CHART_TYPES = {
  bar: {
    series: (data, name) => ({ type: 'bar', name, data, itemStyle: { borderRadius: [4, 4, 0, 0] } }),
    yAxis: { type: 'value' }
  },
  line: {
    series: (data, name) => ({ type: 'line', name, data, smooth: true, symbol: 'circle', symbolSize: 6 }),
    yAxis: { type: 'value' }
  },
  area: {
    series: (data, name) => ({ type: 'line', name, data, smooth: true, areaStyle: { opacity: 0.3 }, symbol: 'none' }),
    yAxis: { type: 'value' }
  },
  pie: {
    series: (data, name) => ({ type: 'pie', name, data, radius: ['40%', '70%'], label: { show: true, formatter: '{b}: {d}%' } }),
    yAxis: null,
    xAxis: null
  },
  scatter: {
    series: (data, name) => ({ type: 'scatter', name, data, symbolSize: 10 }),
    yAxis: { type: 'value' }
  },
  stacked_bar: {
    series: (data, name) => ({ type: 'bar', name, data, stack: 'total', itemStyle: { borderRadius: 0 } }),
    yAxis: { type: 'value' }
  }
}

export function useCharts() {
  const instances = {}

  function getChartConfig(type, categoryData, valueKeys, rows) {
    const config = CHART_TYPES[type] || CHART_TYPES.bar

    const option = {
      tooltip: { trigger: type === 'pie' ? 'item' : 'axis' },
      legend: { data: valueKeys, top: 0, textStyle: { fontSize: 12 } },
      grid: config.xAxis !== null ? { left: 50, right: 20, top: 40, bottom: 50 } : undefined,
      xAxis: config.xAxis !== null ? { type: 'category', data: categoryData, axisLabel: { rotate: 30, fontSize: 11 } } : undefined,
      yAxis: config.yAxis,
      series: []
    }

    if (type === 'pie') {
      option.series.push(config.series(categoryData.map((cat, i) => ({
        name: cat,
        value: Number(rows[i] && rows[i][valueKeys[0]]) || 0
      })), ''))
    } else {
      valueKeys.forEach(k => {
        option.series.push(config.series(rows.map(r => Number(r[k]) || 0), k))
      })
    }

    return option
  }

  function initChart(el, type, categoryData, valueKeys, rows) {
    if (!el) return null
    const chart = echarts.init(el)
    chart.setOption(getChartConfig(type, categoryData, valueKeys, rows))
    return chart
  }

  function disposeChart(el) {
    if (el) {
      const chart = echarts.getInstanceByDom(el)
      if (chart) chart.dispose()
    }
  }

  function resizeAll() {
    Object.values(instances).forEach(chart => {
      try { chart.resize() } catch (e) { /* ignore */ }
    })
  }

  window.addEventListener('resize', resizeAll)
  onUnmounted(() => {
    window.removeEventListener('resize', resizeAll)
    Object.values(instances).forEach(c => { try { c.dispose() } catch {} })
  })

  return { getChartConfig, initChart, disposeChart, resizeAll, CHART_TYPES, instances }
}
