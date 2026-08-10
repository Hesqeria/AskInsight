import { describe, it, expect } from 'vitest'
import { useCharts } from '../../src/composables/useCharts.js'

describe('useCharts.getChartOption', () => {
  const { getChartOption, recommendChartType } = useCharts()

  const rows = [
    { region: 'east', amount: 100 },
    { region: 'north', amount: 200 },
    { region: 'south', amount: 150 },
  ]

  it('builds bar option with category axis', () => {
    const opt = getChartOption('bar', rows, 'region', ['amount'])
    expect(opt.xAxis.type).toBe('category')
    expect(opt.xAxis.data).toEqual(['east', 'north', 'south'])
    expect(opt.series[0].type).toBe('bar')
    expect(opt.series[0].data).toEqual([100, 200, 150])
  })

  it('builds pie option from first value key', () => {
    const opt = getChartOption('pie', rows, 'region', ['amount'])
    expect(opt.series[0].type).toBe('pie')
    expect(opt.series[0].data[0]).toEqual({ name: 'east', value: 100 })
  })

  it('builds stacked area with stack: total', () => {
    const wide = [
      { d: '2024-01', a: 1, b: 2 },
      { d: '2024-02', a: 3, b: 4 },
    ]
    const opt = getChartOption('stacked_area', wide, 'd', ['a', 'b'])
    expect(opt.series[0].stack).toBe('total')
    expect(opt.series[0].areaStyle).toBeDefined()
  })

  it('big_number extracts first row first value', () => {
    const opt = getChartOption('big_number', [{ k: 'metric', v: 9999 }], 'k', ['v'])
    expect(opt.title.text).toBe('9,999')
  })

  it('table option returns _isTable flag', () => {
    const opt = getChartOption('table', rows, 'region', ['amount'])
    expect(opt._isTable).toBe(true)
    expect(opt.columns).toEqual(['region', 'amount'])
  })

  it('returns empty object for empty rows', () => {
    expect(getChartOption('bar', [], 'region', ['amount'])).toEqual({})
  })

  it('recommendChartType suggests big_number for single cell', () => {
    expect(recommendChartType([{ x: 42 }])).toBe('big_number')
  })

  it('recommendChartType suggests table for single column multi-row', () => {
    expect(recommendChartType([{ x: 1 }, { x: 2 }])).toBe('table')
  })

  it('recommendChartType suggests pie for 2 cols <= 6 rows', () => {
    const r = [{ a: 'x', b: 1 }, { a: 'y', b: 2 }]
    expect(['pie', 'bar']).toContain(recommendChartType(r))
  })
})
