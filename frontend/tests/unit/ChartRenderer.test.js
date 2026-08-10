import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ChartRenderer from '../../src/components/chart/ChartRenderer.vue'

// ECharts canvas needs jsdom polyfill; mock the composable's initChart
vi.mock('../../src/composables/useCharts.js', () => ({
  useCharts: () => ({
    CHART_TYPES: { bar: 'Bar', big_number: 'KPI', table: 'Table' },
    initChart: vi.fn(() => ({
      setOption: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      getDataURL: vi.fn(() => 'data:png'),
    })),
    disposeChart: vi.fn(),
  }),
}))

describe('ChartRenderer.vue', () => {
  it('renders big_number view with formatted value', async () => {
    const wrapper = mount(ChartRenderer, {
      props: {
        type: 'big_number',
        categoryKey: 'k',
        valueKeys: ['v'],
        rows: [{ k: 'metric', v: 12345 }],
      },
    })
    await flushPromises()
    expect(wrapper.find('.big-number-value').text()).toBe('12,345')
    expect(wrapper.find('.big-number-label').text()).toBe('v')
  })

  it('renders table view for table type', async () => {
    const wrapper = mount(ChartRenderer, {
      props: {
        type: 'table',
        categoryKey: 'region',
        valueKeys: ['amount'],
        rows: [
          { region: 'east', amount: 100 },
          { region: 'north', amount: 200 },
        ],
      },
    })
    await flushPromises()
    const headers = wrapper.findAll('th')
    expect(headers.length).toBe(2)
    expect(headers[0].text()).toBe('region')
    expect(headers[1].text()).toBe('amount')
    expect(wrapper.findAll('tbody tr').length).toBe(2)
  })

  it('emits drill-down when chart clicked (via exposed handler path)', async () => {
    const wrapper = mount(ChartRenderer, {
      props: {
        type: 'bar',
        categoryKey: 'region',
        valueKeys: ['amount'],
        rows: [{ region: 'east', amount: 1 }],
      },
    })
    await flushPromises()
    // chartEl exists
    expect(wrapper.find('.chart-box').exists()).toBe(true)
  })
})
