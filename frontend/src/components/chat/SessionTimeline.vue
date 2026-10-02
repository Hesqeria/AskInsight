<template>
  <el-drawer :model-value="open" :title="t('timelineTitle')" size="420px" @close="emit('close')">
    <div v-if="loading" class="tl-loading">loading…</div>
    <div v-else-if="error" class="tl-error">{{ error }}</div>
    <template v-else>
      <div class="tl-meta">
        <span>{{ timeline.event_count }} events</span>
        <span v-if="timeline.turns?.length">{{ timeline.turns.length }} turn(s)</span>
      </div>
      <el-timeline>
        <el-timeline-item
          v-for="st in timeline.stages" :key="st.seq"
          :type="nodeColor(st.type)" :timestamp="st.created_at" placement="top"
          :hollow="!isKey(st.type)"
        >
          <div class="tl-type">{{ st.type }}</div>
          <pre class="tl-payload">{{ pretty(st.payload) }}</pre>
        </el-timeline-item>
      </el-timeline>
      <div v-if="timeline.guard_decisions?.length" class="tl-section">
        <h4>{{ t('timeline.guards') }}</h4>
        <div v-for="(g, i) in timeline.guard_decisions" :key="i" class="tl-guard" :class="g.outcome">
          {{ g.guard }} → {{ g.outcome }} <span v-if="g.ms">({{ g.ms }}ms)</span>
          <div v-if="g.reason" class="tl-guard-reason">{{ g.reason }}</div>
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useI18n } from '../../utils/i18n.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  sessionId: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const { t } = useI18n()
const timeline = ref({})
const loading = ref(false)
const error = ref('')

watch(() => [props.open, props.sessionId], async ([open, sid]) => {
  if (!open || !sid) return
  loading.value = true
  error.value = ''
  timeline.value = {}
  try {
    const token = localStorage.getItem('token') || ''
    const resp = await fetch(`/api/v1/sessions/${sid}/events/timeline`, {
      headers: token ? { Authorization: 'Bearer ' + token } : {},
    })
    const body = await resp.json()
    if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`)
    timeline.value = body
  } catch (e) {
    error.value = e?.message || 'load failed'
  } finally {
    loading.value = false
  }
})

const KEY_TYPES = ['query/received', 'sql/generated', 'sql/executed', 'guard/decision', 'turn/ended', 'plan/clarified']
function isKey(type) { return KEY_TYPES.includes(type) }
function nodeColor(type) {
  if (type === 'turn/ended') return 'success'
  if (type === 'guard/decision') return 'warning'
  if (type === 'sql/generated' || type === 'sql/executed') return 'primary'
  return 'info'
}
function pretty(payload) {
  if (!payload || !Object.keys(payload).length) return ''
  return JSON.stringify(payload, null, 1).slice(0, 500)
}
</script>

<style scoped>
.tl-loading, .tl-error { color: var(--color-text-secondary); font-size: 13px; padding: 12px; }
.tl-error { color: var(--color-danger); }
.tl-meta { display: flex; gap: 12px; font-size: 12px; color: var(--color-text-secondary); margin-bottom: 8px; }
.tl-type { font-size: 13px; font-weight: 600; font-family: monospace; }
.tl-payload { margin: 4px 0 0; font-size: 11px; color: var(--color-text-secondary); white-space: pre-wrap; word-break: break-all; background: var(--color-bg-tertiary); padding: 6px; border-radius: 6px; }
.tl-section h4 { margin: 12px 0 6px; font-size: 13px; }
.tl-guard { font-size: 12px; font-family: monospace; padding: 2px 0; }
.tl-guard.pass { color: var(--color-success); }
.tl-guard.reject { color: var(--color-danger); }
.tl-guard.ask { color: #e6a23c; }
.tl-guard-reason { color: var(--color-text-secondary); font-family: inherit; margin-top: 2px; }
</style>
