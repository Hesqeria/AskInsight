<template>
  <div class="kpi-wrap">
    <div class="kpi-controls">
      <select class="subject-select subject-key" :value="subject"
              @change="$emit('subjectChange', $event.target.value)">
        <option v-for="sb in subjectOptions" :key="sb.key" :value="sb.key">{{ sb.label }}</option>
      </select>
      <select class="subject-select" :value="timeType"
              @change="$emit('timeTypeChange', $event.target.value)">
        <option v-for="t in timeTypes" :key="t.key" :value="t.key">{{ t.label }}</option>
      </select>
      <div class="win-tabs">
        <button v-for="w in ['yesterday', '7d', '30d', 'month']" :key="w"
                class="win-tab" :class="{ active: window === w }"
                @click="$emit('windowChange', w)">{{ winLabel(w) }}</button>
      </div>
      <div v-if="subjects.regions.length && currentSubjectDef?.geo" class="subject-row">
        <select class="subject-select" :value="region"
                @change="$emit('regionChange', $event.target.value)">
          <option value="">全部区域</option>
          <option v-for="r in subjects.regions" :key="r" :value="r">{{ r }}</option>
        </select>
        <select class="subject-select" :value="province"
                :disabled="!region"
                @change="$emit('provinceChange', $event.target.value)">
          <option value="">全部省份</option>
          <option v-for="p in provinceOptions" :key="p" :value="p">{{ p }}</option>
        </select>
      </div>
    </div>
    <div v-if="cards[0]" class="subject-line">
      主体:{{ cards[0].subject_line }} <span class="caliber">{{ caliber }}</span>
    </div>
    <div class="kpi-row">
      <div v-for="card in cards" :key="card.metric" class="kpi-card"
           @click="$emit('ask', card.question)">
        <div class="kpi-label">{{ card.metric }}</div>
        <div class="kpi-value">{{ formatValue(card.value, card.unit) }}</div>
        <div class="kpi-meta">
          <span v-if="card.delta_pct !== null && card.delta_pct !== undefined"
                class="kpi-delta" :class="card.delta_pct >= 0 ? 'up' : 'down'">
            {{ card.delta_pct >= 0 ? '↑' : '↓' }} {{ Math.abs(card.delta_pct) }}%
          </span>
          <span class="kpi-window">{{ card.window }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  cards: { type: Array, default: () => [] },
  window: { type: String, default: '7d' },
  subject: { type: String, default: 'order' },
  timeType: { type: String, default: '' },
  region: { type: String, default: '' },
  province: { type: String, default: '' },
  caliber: { type: String, default: '' },
  subjects: { type: Object, default: () => ({ regions: [], provinces: [], subjects: [] }) },
})
defineEmits(['ask', 'windowChange', 'subjectChange', 'timeTypeChange', 'regionChange', 'provinceChange'])

const subjectOptions = computed(() => props.subjects.subjects || [])
const currentSubjectDef = computed(() =>
  subjectOptions.value.find(s => s.key === props.subject) || subjectOptions.value[0])
const timeTypes = computed(() =>
  (currentSubjectDef.value?.time_semantics) || [])

const provinceOptions = computed(() =>
  (props.subjects.provinces || [])
    .filter(p => !props.region || p.region === props.region)
    .map(p => p.province))


function winLabel(w) {
  return { yesterday: '昨天', '7d': '近7天', '30d': '近30天', month: '本月' }[w] || w
}

function formatValue(v, unit) {
  if (v === null || v === undefined) return '-'
  const body = Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return unit ? `${body} ${unit}` : body
}
</script>

<style scoped>
.kpi-wrap { max-width: 720px; }
.kpi-controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 10px; }
.win-tabs { display: inline-flex; border: 1px solid rgba(14,124,107,0.3); border-radius: 8px; overflow: hidden; }
.win-tab { font-size: 12px; padding: 4px 12px; border: none; background: transparent; color: var(--text-secondary, #6B7370); cursor: pointer; }
.win-tab.active { background: #0E7C6B; color: #fff; }
.subject-row { display: inline-flex; gap: 8px; }
.subject-select { font-size: 12px; padding: 4px 8px; border: 1px solid var(--border-color, #D8DEDC); border-radius: 8px; background: var(--bg-card, #fff); color: var(--text-primary, #2A2E2C); }
.subject-select:disabled { opacity: 0.5; }
.subject-line { font-size: 12px; color: #0E7C6B; margin-bottom: 8px; }
.caliber { font-size: 10px; color: var(--text-secondary, #9AA3A0); border: 1px solid var(--border-color, #D8DEDC); padding: 0 8px; border-radius: 999px; margin-left: 8px; }
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 4px 0 18px; }
.kpi-card { padding: 14px 16px; background: linear-gradient(160deg, rgba(14,124,107,0.09), rgba(14,124,107,0.02)); border: 1px solid rgba(14,124,107,0.22); border-radius: 12px; cursor: pointer; transition: transform 0.15s ease, box-shadow 0.15s ease; }
.kpi-card:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(14,124,107,0.18); }
.kpi-label { font-size: 12px; font-weight: 600; letter-spacing: 0.06em; color: #0E7C6B; }
.kpi-value { margin-top: 6px; font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text-primary, #2A2E2C); }
.kpi-meta { margin-top: 6px; display: flex; align-items: center; gap: 8px; font-size: 11px; }
.kpi-delta { font-weight: 600; padding: 1px 8px; border-radius: 999px; }
.kpi-delta.up { color: #0E7C6B; background: rgba(14,124,107,0.12); }
.kpi-delta.down { color: #C0392B; background: rgba(192,57,43,0.10); }
.kpi-window { color: var(--text-secondary, #9AA3A0); }
</style>
