export function useDashboardTemplates() {
  const templates = [
    {
      key: 'sales',
      name: '销售概览',
      widgets: [
        { x: 0, y: 0, w: 3, h: 4, config: { type: 'big_number', title: '总销售额', categoryKey: 'metric', valueKeys: ['amount'] } },
        { x: 3, y: 0, w: 9, h: 4, config: { type: 'line', title: '销售趋势', categoryKey: 'date', valueKeys: ['amount'] } },
        { x: 0, y: 4, w: 6, h: 5, config: { type: 'bar', title: '区域销售', categoryKey: 'region', valueKeys: ['amount'] } },
        { x: 6, y: 4, w: 6, h: 5, config: { type: 'pie', title: '品类占比', categoryKey: 'category', valueKeys: ['amount'] } },
      ],
    },
    {
      key: 'users',
      name: '用户分析',
      widgets: [
        { x: 0, y: 0, w: 3, h: 4, config: { type: 'big_number', title: '总用户数', categoryKey: 'metric', valueKeys: ['users'] } },
        { x: 3, y: 0, w: 9, h: 4, config: { type: 'area', title: '新增用户', categoryKey: 'date', valueKeys: ['new_users'] } },
        { x: 0, y: 4, w: 6, h: 5, config: { type: 'funnel', title: '转化漏斗', categoryKey: 'step', valueKeys: ['users'] } },
        { x: 6, y: 4, w: 6, h: 5, config: { type: 'table', title: '活跃用户明细', categoryKey: 'date', valueKeys: ['users', 'sessions'] } },
      ],
    },
    {
      key: 'product',
      name: '商品分析',
      widgets: [
        { x: 0, y: 0, w: 6, h: 5, config: { type: 'treemap', title: '类目分布', categoryKey: 'category', valueKeys: ['sales'] } },
        { x: 6, y: 0, w: 6, h: 5, config: { type: 'bar', title: 'TOP 商品', categoryKey: 'product', valueKeys: ['sales'] } },
        { x: 0, y: 5, w: 6, h: 5, config: { type: 'heatmap', title: '库存热力', categoryKey: 'warehouse', valueKeys: ['category', 'stock'] } },
        { x: 6, y: 5, w: 6, h: 5, config: { type: 'table', title: '库存明细', categoryKey: 'product', valueKeys: ['stock', 'sales'] } },
      ],
    },
    {
      key: 'ops',
      name: '运营监控',
      widgets: [
        { x: 0, y: 0, w: 3, h: 4, config: { type: 'gauge', title: '目标完成率', categoryKey: 'metric', valueKeys: ['completion'] } },
        { x: 3, y: 0, w: 9, h: 4, config: { type: 'stacked_area', title: '多指标趋势', categoryKey: 'date', valueKeys: ['orders', 'refunds'] } },
        { x: 0, y: 4, w: 6, h: 5, config: { type: 'radar', title: '综合能力', categoryKey: 'dimension', valueKeys: ['score'] } },
        { x: 6, y: 4, w: 6, h: 5, config: { type: 'dual_axis', title: '订单与客单价', categoryKey: 'date', valueKeys: ['orders', 'avg_price'] } },
      ],
    },
    {
      key: 'finance',
      name: '财务分析',
      widgets: [
        { x: 0, y: 0, w: 3, h: 4, config: { type: 'big_number', title: '收入', categoryKey: 'metric', valueKeys: ['revenue'] } },
        { x: 3, y: 0, w: 3, h: 4, config: { type: 'big_number', title: '成本', categoryKey: 'metric', valueKeys: ['cost'] } },
        { x: 6, y: 0, w: 6, h: 4, config: { type: 'line', title: '利润趋势', categoryKey: 'date', valueKeys: ['profit'] } },
        { x: 0, y: 4, w: 6, h: 5, config: { type: 'stacked_bar', title: '收支构成', categoryKey: 'month', valueKeys: ['revenue', 'cost'] } },
        { x: 6, y: 4, w: 6, h: 5, config: { type: 'table', title: '财务明细', categoryKey: 'month', valueKeys: ['revenue', 'cost', 'profit'] } },
      ],
    },
  ]

  function getTemplate(key) {
    return templates.find(t => t.key === key)
  }

  function applyTemplate(key, sampleRows = []) {
    const tpl = getTemplate(key)
    if (!tpl) return []
    return tpl.widgets.map((w, idx) => ({
      id: `${Date.now()}-${idx}`,
      x: w.x,
      y: w.y,
      w: w.w,
      h: w.h,
      config: { ...w.config },
      rows: sampleRows,
      layout: true,
    }))
  }

  return { templates, getTemplate, applyTemplate }
}
