import { ref, watch } from 'vue'

const locale = ref(localStorage.getItem('askinsight_lang') || 'en')

watch(locale, (v) => localStorage.setItem('askinsight_lang', v))

const messages = {
  en: {
    title: 'AskInsight',
    subtitle: 'Ask questions about your data in natural language',
    chat: 'Chat',
    schema: 'Schema',
    settings: 'Settings',
    pipelines: 'Pipelines',
    readiness: 'Readiness',
    history: 'History',
    noQueries: 'No queries yet',
    favorites: 'Favorites',
    noFavorites: 'No favorites',
    ask: 'Ask',
    processing: 'Processing...',
    showSQL: 'Show SQL',
    copy: 'Copy',
    exportCSV: 'Export CSV',
    star: 'Star',
    unstar: 'Unstar',
    placeholder: 'Ask your data question...',
    llmModel: 'LLM Model',
    datasource: 'Datasource',
    language: 'Language',
    schemaBrowser: 'Schema Browser',
    cols: 'cols',
    welcome: 'AskInsight',
    examples: ['Top regions by sales', 'Brand sales share', 'Top 3 customers by spend', 'Monthly sales trend', 'High-value customers', 'Member tier comparison'],
    pendingTasks: 'Pending on you',
    timeline: 'Timeline',
    timelineTitle: 'Session Timeline',
    'approval.pending': 'Approval Required',
    'approval.approve': 'Approve',
    'approval.reject': 'Reject',
    'approval.waiting': 'Waiting for an approver (L3+ role required)...',
    'timeline.query': 'Query received',
    'timeline.guards': 'Guard decisions',
    'timeline.turns': 'Turns',
    'timeline.noData': 'No events for this session yet.',
  },
  cn: {
    title: '智能问数',
    subtitle: '用自然语言询问您的数据问题',
    chat: '对话',
    schema: '数据模型',
    settings: '设置',
    pipelines: '管道',
    readiness: '准入评分',
    history: '历史',
    noQueries: '暂无查询',
    favorites: '收藏',
    noFavorites: '暂无收藏',
    ask: '提问',
    processing: '分析中...',
    showSQL: '查看 SQL',
    copy: '复制',
    exportCSV: '导出 CSV',
    star: '收藏',
    unstar: '取消收藏',
    placeholder: '输入您的数据问题...',
    llmModel: 'LLM 模型',
    datasource: '数据源',
    language: '语言',
    schemaBrowser: '数据模型浏览',
    cols: '列',
    welcome: '智能问数',
    examples: ['各区域销售额排行', '品牌销售占比', '消费金额 Top3 客户', '月度销售趋势', '高价值客户品类', '会员等级对比'],
    pendingTasks: '待处理',
    timeline: '时间线',
    timelineTitle: '会话时间线',
    'approval.pending': '需要审批',
    'approval.approve': '批准',
    'approval.reject': '拒绝',
    'approval.waiting': '等待审批人处理(需 L3 及以上角色)…',
    'timeline.query': '收到问题',
    'timeline.guards': '守卫决策',
    'timeline.turns': '轮次',
    'timeline.noData': '该会话暂无事件。',
  },
}

export function useI18n() {
  const t = (key) => {
    const msg = messages[locale.value] || messages.en
    return msg[key] || key
  }
  return { locale, t }
}

export default { useI18n }
