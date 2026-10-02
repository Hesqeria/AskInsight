<template>
  <el-drawer
    :model-value="modelValue"
    :title="'调整查询理解'"
    size="480px"
    @close="emit('update:modelValue', false)"
  >
    <!-- Current understanding summary -->
    <div v-if="card && card.current_summary" class="current-summary">
      <div class="summary-label">当前理解</div>
      <div class="summary-text">{{ card.current_summary }}</div>
    </div>

    <!-- Field editor groups -->
    <el-form label-position="top" size="default">
      <!-- Measure -->
      <el-form-item v-if="measureOptions.length" label="指标">
        <el-select
          v-model="draft.measure"
          placeholder="选择指标"
          clearable
          style="width: 100%"
        >
          <el-option
            v-for="opt in measureOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          >
            <span>{{ opt.label }}</span>
            <span v-if="opt.description" class="option-desc">{{ opt.description }}</span>
          </el-option>
        </el-select>
      </el-form-item>

      <!-- Time -->
      <el-form-item label="时间范围">
        <el-select
          v-model="draft.time"
          placeholder="选择时间范围"
          clearable
          allow-create
          filterable
          style="width: 100%"
        >
          <el-option v-for="opt in timeOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
      </el-form-item>

      <!-- Group by (multi) -->
      <el-form-item label="分组维度(可多选)">
        <el-select
          v-model="draft.group_by"
          placeholder="选择分组维度"
          multiple
          clearable
          style="width: 100%"
        >
          <el-option
            v-for="opt in groupByOptions"
            :key="String(opt.value)"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>

      <!-- Dimension filter (free-form region/value) -->
      <el-form-item label="筛选条件">
        <el-input
          v-model="draft.dimension_text"
          placeholder="如: 地域 = 华北"
        />
        <div class="field-hint">格式: 字段 = 值(可选,留空跳过)</div>
      </el-form-item>
    </el-form>

    <!-- Live preview -->
    <div v-if="previewSummary" class="preview">
      <div class="summary-label">调整后理解</div>
      <div class="summary-text">{{ previewSummary }}</div>
    </div>

    <!-- Error -->
    <div v-if="store.error" class="error-msg">{{ store.error }}</div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button
        type="primary"
        :loading="store.submitting"
        :disabled="!hasSelection"
        @click="applyAndConfirm"
      >
        应用调整并执行
      </el-button>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { useClarifyStore } from '../../stores/clarify.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  card: { type: Object, required: true },
})

const emit = defineEmits(['update:modelValue', 'confirmed'])
const store = useClarifyStore()

const draft = reactive({
  measure: null,
  time: null,
  group_by: [],
  dimension_text: '',
})

// Pre-fill from card suggestions when available.
watch(
  () => props.card,
  () => {
    if (!props.card) return
    draft.measure = null
    draft.time = null
    draft.group_by = []
    draft.dimension_text = ''
  },
  { immediate: true },
)

// Extract options from the card's suggestion groups.
const measureOptions = computed(() => {
  const g = (props.card.suggestions || []).find(x => x.field === 'measure')
  return g ? g.options : []
})
const timeOptions = computed(() => {
  const g = (props.card.suggestions || []).find(x => x.field === 'time')
  return g ? g.options : []
})
const groupByOptions = computed(() => {
  const g = (props.card.suggestions || []).find(x => x.field === 'group_by')
  return g ? g.options.filter(o => o.value !== null) : []
})

const hasSelection = computed(() => {
  return draft.measure || draft.time || (draft.group_by && draft.group_by.length) || draft.dimension_text.trim()
})

// Rough preview: build a mini summary from current draft.
const previewSummary = computed(() => {
  const parts = []
  if (draft.measure) {
    const opt = measureOptions.value.find(o => o.value === draft.measure)
    parts.push(`指标: ${opt ? opt.label : draft.measure}`)
  }
  if (draft.time) parts.push(`时间: ${draft.time}`)
  if (draft.group_by && draft.group_by.length) {
    const names = draft.group_by.map(v => {
      const o = groupByOptions.value.find(x => x.value === v)
      return o ? o.label : v
    })
    parts.push(`分组: ${names.join('、')}`)
  }
  if (draft.dimension_text.trim()) parts.push(`筛选: ${draft.dimension_text.trim()}`)
  return parts.length ? parts.join(' · ') : ''
})

async function applyAndConfirm() {
  // Parse dimension text "地域 = 华北" → class_id + value (basic parser).
  const selections = {}
  if (draft.measure) selections.measure = draft.measure
  if (draft.time) selections.time = draft.time
  if (draft.group_by && draft.group_by.length) selections.group_by = draft.group_by

  // Populate store selections then submit confirmed.
  store.select('measure', draft.measure)
  store.select('time', draft.time)
  if (draft.group_by && draft.group_by.length) store.setGroupBy(draft.group_by)
  if (draft.dimension_text.trim()) {
    const dim = parseDimensionText(draft.dimension_text)
    if (dim) store.select('dimension', dim)
  }

  try {
    await store.submitResponse({ responseType: 'selection', confirmed: true })
    emit('update:modelValue', false)
    emit('confirmed')
  } catch (e) {
    // Error already surfaced via store.error.
  }
}

function parseDimensionText(text) {
  const m = text.match(/(.+?)\s*[=＝]\s*(.+)/)
  if (!m) return null
  const [, field, value] = m
  // Map common Chinese field names to class IDs.
  const map = {
    '地区': 'C050', '地域': 'C050', '区域': 'C050',
    '商品': 'C020', '品类': 'C021', '客户': 'C011', '用户': 'C011',
  }
  const classId = map[field.trim()] || 'C050'
  return { class_id: classId, value: value.trim(), operator: '=' }
}
</script>

<style scoped>
.current-summary { margin-bottom: 16px; padding: 10px 12px; background: #f5f7fa; border-radius: 8px; }
.summary-label { font-size: 12px; color: #909399; margin-bottom: 4px; }
.summary-text { font-size: 14px; color: #303133; }
.option-desc { float: right; color: #909399; font-size: 12px; margin-left: 8px; }
.field-hint { font-size: 12px; color: #c0c4cc; margin-top: 4px; }
.preview { margin-top: 16px; padding: 10px 12px; background: #f0f9eb; border-radius: 8px; }
.error-msg { margin-top: 12px; color: #f56c6c; font-size: 13px; }
</style>
