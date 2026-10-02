<template>
  <div class="welcome-view">
    <div class="welcome-mark">问</div>
    <h1 class="welcome-title">{{ title }}</h1>
    <p class="welcome-subtitle">{{ subtitle }}</p>
    <KpiCardRow :cards="kpiCards"
                :key="kpiSubject + kpiWindow + kpiTimeType + kpiRegion + kpiProvince"
                :window="kpiWindow" :subject="kpiSubject" :time-type="kpiTimeType"
                :region="kpiRegion" :province="kpiProvince"
                :caliber="kpiCaliber" :subjects="kpiSubjects"
                @ask="(q) => emit('quickAsk', q)"
                @window-change="loadKpi"
                @subject-change="onSubjectChange"
                @time-type-change="onTimeTypeChange"
                @region-change="onRegionChange"
                @province-change="onProvinceChange" />
    <div class="example-grid">
      <div v-for="(ex, i) in currentExamples" :key="i" class="example-card" @click="emit('quickAsk', ex.query)">
        <div class="emoji">{{ ex.icon }}</div>
        <div class="title">{{ ex.label }}</div>
        <div class="desc">{{ ex.desc }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import KpiCardRow from './KpiCardRow.vue'

const props = defineProps({ locale: { type: String, default: 'en' } })
const emit = defineEmits(['quickAsk'])
const kpiCards = ref([])
const kpiWindow = ref('7d')
const kpiTimeType = ref('paid')
const kpiSubject = ref('order')
const kpiRegion = ref('')
const kpiProvince = ref('')
const kpiCaliber = ref('')
const kpiSubjects = ref({ regions: [], provinces: [], subjects: [] })

async function loadKpi(win) {
  if (win) kpiWindow.value = win
  try {
    const token = localStorage.getItem('token') || ''
    const params = new URLSearchParams({
      subject: kpiSubject.value, window: kpiWindow.value,
      time_type: kpiTimeType.value === 'paid' && kpiSubject.value !== 'order' ? '' : kpiTimeType.value,
    })
    if (kpiRegion.value) params.set('region', kpiRegion.value)
    if (kpiProvince.value) params.set('province', kpiProvince.value)
    const resp = await fetch(`/api/kpi/cards?${params}`, {
      headers: token ? { Authorization: 'Bearer ' + token } : {},
    })
    if (resp.status === 401) {
      localStorage.removeItem('token')
      location.href = '/login'
      return
    }
    if (resp.status === 429) {
      kpiCaliber.value = '请求过于频繁，请稍候重试'
      return
    }
    if (resp.ok) {
      const data = await resp.json()
      kpiCards.value = data.cards || []
      kpiCaliber.value = data.caliber || ''
    }
  } catch { /* cards stay hidden on failure */ }
}

function onSubjectChange(sb) {
  kpiSubject.value = sb
  kpiTimeType.value = ''   // per-subject default semantics
  kpiRegion.value = ''
  kpiProvince.value = ''
  loadKpi()
}

function onTimeTypeChange(t) {
  kpiTimeType.value = t
  loadKpi()
}

function onRegionChange(r) {
  kpiRegion.value = r
  kpiProvince.value = ''   // province list is region-scoped
  loadKpi()
}

function onProvinceChange(p) {
  kpiProvince.value = p
  loadKpi()
}

onMounted(async () => {
  loadKpi()
  try {
    const token = localStorage.getItem('token') || ''
    const resp = await fetch('/api/kpi/subjects', {
      headers: token ? { Authorization: 'Bearer ' + token } : {},
    })
    if (resp.status === 401) {
      localStorage.removeItem('token')
      location.href = '/login'
      return
    }
    if (resp.ok) kpiSubjects.value = await resp.json()
  } catch { /* selectors stay empty */ }
})

const examplesMap = {
  en: {
    title: 'AskInsight',
    subtitle: 'Ask questions about your data in natural language',
    examples: [
      { icon: '📊', label: 'Revenue by Region', desc: 'Top regions by sales amount', query: 'Top regions by sales' },
      { icon: '🏷️', label: 'Brand Share', desc: 'Brand sales proportion', query: 'Brand sales share' },
      { icon: '🏆', label: 'Top Customers', desc: 'Top 3 customers by spend', query: 'Top 3 customers by spend' },
      { icon: '📈', label: 'Monthly Trend', desc: 'Monthly sales trend over time', query: 'Monthly sales trend' },
      { icon: '💎', label: 'High-Value Users', desc: 'High-value customers by category', query: 'High-value customer categories' },
      { icon: '👥', label: 'Member Tiers', desc: 'Member tier comparison', query: 'Member tier comparison' },
    ]
  },
  cn: {
    title: 'AskInsight 智能问数',
    subtitle: '用自然语言提问,即刻获得数据洞察',
    examples: [
      { icon: '📊', label: '各区域GMV排行', desc: '按销售额对比各区域表现', query: '各区域GMV排行' },
      { icon: '🏷️', label: '品牌销售占比', desc: '各品牌销售额占比分布', query: '品牌销售占比' },
      { icon: '🏆', label: '消费TOP3客户', desc: '按消费金额排名的客户', query: '消费金额最高的前3个客户' },
      { icon: '📈', label: 'GMV月度趋势', desc: 'GMV按月的走势变化', query: 'GMV按月趋势' },
      { icon: '💎', label: '高价值用户品类', desc: '高价值客户的品类偏好', query: '高价值客户的品类分布' },
      { icon: '👥', label: '会员等级对比', desc: '各会员等级的人数与消费', query: '会员等级对比' },
    ]
  },
}

const current = computed(() => examplesMap[props.locale] || examplesMap.en)
const title = computed(() => current.value.title)
const subtitle = computed(() => current.value.subtitle)
const currentExamples = computed(() => current.value.examples)
</script>

<style scoped>
.welcome-mark {
  width: 64px; height: 64px;
  border-radius: 18px;
  background: linear-gradient(135deg, #2E9B87, #0E7C6B);
  color: #F2FBF8;
  font-size: 30px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 18px;
  box-shadow: 0 6px 20px rgba(14, 124, 107, 0.35);
}
</style>
