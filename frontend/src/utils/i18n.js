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
